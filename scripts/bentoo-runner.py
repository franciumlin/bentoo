#!/usr/bin/env python2.7
# coding: utf-8
''' Runner - Versatile testcase runner

Runner run a hierarchy of test cases and store the results in another
hierarchy.  It provides options such as test case filter, timeout etc, to make
repeated test easy.
'''

import os
import sys
import argparse
import re
import fnmatch
import json
import string
import subprocess
import pprint
from collections import OrderedDict


class TestProjectReader:

    '''Scan a test project for test cases'''

    def __init__(self, project_root):
        '''Create a scanner object for project at 'project_dir' '''
        self.project_root = os.path.abspath(project_root)
        conf_fn = os.path.join(self.project_root, "TestProject.json")
        if not os.path.exists(conf_fn):
            raise RuntimeError("Invalid project directory: %s" % project_root)
        conf = json.load(file(conf_fn))
        version = conf.get("version", 1)
        if version != 1:
            raise RuntimeError(
                "Unsupported project version '%s': Only 1 " % version)
        self.name = conf["name"]
        self.test_factors = conf["test_factors"]
        self.data_files = conf["data_files"]
        self.test_cases = conf["test_cases"]

        self.last_stats = None
        stats_fn = os.path.join(self.project_root, "run_stats.json")
        if os.path.exists(stats_fn):
            self.last_stats = json.load(file(stats_fn))

    def check(self):
        '''Check project's validity

        Check project's validity by checking the existance of each case's
        working directories and specification file. Specification content may
        be checked in the future.

        Exceptions:
            RuntimeError: Any error found in the check

            This shall be refined in the future.

        '''
        for k, v in self.test_cases.iteritems():
            case_fullpath = os.path.join(self.project_root, v)
            if not os.path.exists(case_fullpath):
                raise RuntimeError(
                    "Test case '%s' not found in '%s'" %
                    (k, case_fullpath))
            case_spec_fullpath = os.path.join(case_fullpath, "TestCase.json")
            if not os.path.exists(case_spec_fullpath):
                raise RuntimeError(
                    "Test case spec for '%s' is not found in '%s'" %
                    (k, case_fullpath))
            # TODO: check the content of case spec (better with json-schema)

    def itercases(self):
        for case in self.test_cases:
            case_spec_fullpath = os.path.join(
                self.project_root,
                case["path"],
                "TestCase.json")
            case_spec = json.load(file(case_spec_fullpath))
            yield {
                "test_vector": case["test_vector"],
                "path": os.path.join(self.project_root, case["path"]),
                "spec": case_spec
            }

    def count_cases(self):
        return len(self.test_cases)


def has_program(cmd):
    try:
        subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return True
    except OSError:
        return False
    except subprocess.CalledProcessError:
        return True


def shell_quote(x):
    x = str(x)
    if any(i in x for i in set("*?[]${}(); >&")):
        return "\"%s\"" % x
    else:
        return x


def make_bash_script(cmd, envs, outfile):
    content = []
    content.append("#!/bin/bash")
    content.append("#")
    content.append("")
    if envs:
        for k, v in envs.iteritems():
            content.append("export {0}={1}".format(k, shell_quote(v)))
        content.append("")
    content.append(" ".join(map(shell_quote, cmd)))
    file(outfile, "w").write("\n".join(content))
    os.chmod(outfile, 0755)


class MpirunRunner:

    @classmethod
    def is_available(cls):
        if has_program(["mpirun", "-h"]):
            return True
        elif has_program(["mpiexec", "-h"]):
            return True
        else:
            return False

    @classmethod
    def register_cmdline_args(cls, argparser):
        argparser.add_argument("--hosts", default=None,
                               help="Comma seperated host list")
        argparser.add_argument("--ppn", default=None,
                               help="Processes per node")

    @classmethod
    def parse_cmdline_args(cls, namespace):
        return {
            "hosts": namespace.hosts,
            "ppn": namespace.ppn
        }

    def __init__(self, args):
        self.args = args

    def run(self, case, timeout=None, make_script=False, dryrun=False,
            verbose=False, **kwargs):
        test_vector = case["test_vector"]
        path = case["path"]
        spec = case["spec"]
        assert os.path.isabs(path)

        nprocs = str(spec["run"]["nprocs"])
        mpirun_cmd = ["mpirun", "-np", nprocs]
        if self.args["hosts"]:
            mpirun_cmd.extend(["-hosts", self.args["hosts"]])
        if self.args["ppn"]:
            mpirun_cmd.extend(["-ppn", self.args["ppn"]])
        exec_cmd = map(str, spec["cmd"])
        cmd = mpirun_cmd + exec_cmd
        if timeout:
            cmd = ["timeout", "{0}m".format(timeout)] + cmd

        env = dict(os.environ)
        for k, v in spec["envs"].iteritems():
            env[k] = str(v)

        if make_script:
            make_bash_script(cmd, spec["envs"], os.path.join(path, "run.sh"))
        if dryrun:
            return "skipped"

        out_fn = os.path.join(path, "STDOUT")
        err_fn = os.path.join(path, "STDERR")

        if verbose:
            proc1 = subprocess.Popen(cmd, env=env, cwd=path,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT)
            proc2 = subprocess.Popen(["tee", out_fn], cwd=path,
                                     stdin=proc1.stdout)
            proc1.stdout.close()
            ret = proc2.wait()
        else:
            ret = subprocess.call(cmd, env=env, cwd=path,
                                  stdout=file(out_fn, "w"),
                                  stderr=file(err_fn, "w"))

        if ret == 0:
            return "success"
        elif ret == 124:
            return "timeout"
        else:
            return "failed"


class YhrunRunner:

    @classmethod
    def is_available(cls):
        if has_program(["yhrun", "-h"]):
            return True
        else:
            return False

    @classmethod
    def register_cmdline_args(cls, argparser):
        argparser.add_argument("-p", "--partition",
                               metavar="PARTITION", dest="partition",
                               help="Select job partition to use")
        argparser.add_argument("-x", metavar="NODELIST", dest="excluded_nodes",
                               help="Exclude nodes from job allocation")
        argparser.add_argument("-w", metavar="NODELIST", dest="only_nodes",
                               help="Use only selected nodes")
        argparser.add_argument("--batch", action="store_true",
                               help="Use yhbatch instead of yhrun")
        argparser.add_argument("--fix-glex", choices=("none", "v0", "v1"),
                               default="none", help="Fix GLEX settings")

    @classmethod
    def parse_cmdline_args(cls, namespace):
        return {"partition": namespace.partition,
                "excluded_nodes": namespace.excluded_nodes,
                "only_nodes": namespace.only_nodes,
                "use_batch": namespace.batch,
                "fix_glex": namespace.fix_glex}

    def __init__(self, args):
        self.args = args

    def run(self, case, timeout=None, make_script=False, dryrun=False,
            verbose=False, **kwargs):
        test_vector = case["test_vector"]
        path = case["path"]
        spec = case["spec"]
        assert os.path.isabs(path)

        run = spec["run"]
        nprocs = str(run["nprocs"])
        nnodes = run.get("nnodes", None)
        tasks_per_proc = run.get("tasks_per_proc", None)
        yhrun_cmd = ["yhrun"]
        if nnodes:
            yhrun_cmd.extend(["-N", nnodes])
        yhrun_cmd.extend(["-n", nprocs])
        if tasks_per_proc:
            yhrun_cmd.extend(["-c", tasks_per_proc])
        if timeout:
            yhrun_cmd.extend(["-t", str(timeout)])
        if self.args["partition"]:
            yhrun_cmd.extend(["-p", self.args["partition"]])
        if self.args["excluded_nodes"]:
            yhrun_cmd.extend(["-x", self.args["excluded_nodes"]])
        if self.args["only_nodes"]:
            yhrun_cmd.extend(["-w", self.args["only_nodes"]])
        exec_cmd = map(str, spec["cmd"])
        cmd = yhrun_cmd + exec_cmd
        cmd = map(str, cmd)

        env = dict(os.environ)
        for k, v in spec["envs"].iteritems():
            env[k] = str(v)

        if self.args["fix_glex"] == "v0":
            if int(nprocs) > 8192:
                env["PDP_GLEX_USE_HC_MPQ"] = 1
                env["PDP_GLEX_HC_MPQ_L1_CAPACITY"] = 16384
                env["GLEX_BYPASS_RDMA_WRITE_CHANNEL"] = 1
                env["GLEX_EP_MPQ_SLOTS"] = 131072
                env["GLEX_USE_ZC_RNDV"] = 0
        elif self.args["fix_glex"] == "v1":
            if int(nprocs) > 8192:
                env["MPICH_NO_LOCAL"] = 1
                env["GLEX_BYPASS_ER"] = 1
                env["GLEX_USE_ZC_RNDV"] = 0

        if self.args["use_batch"]:
            # build batch job script: we need to remove job control parameters
            # from job command, since they colide with yhbatch parameters.
            real_cmd = list(cmd)
            if "-x" in real_cmd:
                idx = real_cmd.index("-x")
                real_cmd = real_cmd[:idx] + real_cmd[idx + 2:]
            if "-w" in real_cmd:
                idx = real_cmd.index("-w")
                real_cmd = real_cmd[:idx] + real_cmd[idx + 2:]
            if "-p" in real_cmd:
                idx = real_cmd.index("-p")
                real_cmd = real_cmd[:idx] + real_cmd[idx + 2:]
            make_bash_script(real_cmd, spec["envs"],
                             os.path.join(path, "batch_spec.sh"))
            # build yhbatch command line
            yhbatch_cmd = ["yhbatch", "-N", str(nnodes)]
            if self.args["partition"]:
                yhbatch_cmd.extend(["-p", self.args["partition"]])
            if self.args["excluded_nodes"]:
                yhbatch_cmd.extend(["-x", self.args["excluded_nodes"]])
            if self.args["only_nodes"]:
                yhbatch_cmd.extend(["-w", self.args["only_nodes"]])
            yhbatch_cmd.append("./batch_spec.sh")

            if make_script:
                make_bash_script(yhbatch_cmd, None,
                                 os.path.join(path, "run.sh"))
            if dryrun:
                return "skipped"

            subprocess.call(yhbatch_cmd, cwd=path)
            # yhbatch always success
            return "success"

        else:
            if make_script:
                make_bash_script(
                    cmd, spec["envs"], os.path.join(path, "run.sh"))
            if dryrun:
                return "skipped"

            out_fn = os.path.join(path, "STDOUT")
            err_fn = os.path.join(path, "STDERR")

            if verbose:
                proc1 = subprocess.Popen(cmd, env=env, cwd=path,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT)
                proc2 = subprocess.Popen(["tee", out_fn], cwd=path,
                                         stdin=proc1.stdout)
                proc1.stdout.close()
                ret = proc2.wait()
            else:
                ret = subprocess.call(cmd, env=env, cwd=path,
                                      stdout=file(out_fn, "w"),
                                      stderr=file(err_fn, "w"))

            if ret == 0:
                return "success"
            # FIXME: find the correct return code for timeout
            elif ret == 124:
                return "timeout"
            else:
                return "failed"


class BsubRunner:

    @classmethod
    def is_available(cls):
        if has_program(["bsub", "-h"]):
            return True
        else:
            return False

    @classmethod
    def register_cmdline_args(cls, argparser):
        argparser.add_argument("-q", "--queue",
                               metavar="QUEUE", dest="queue",
                               help="Select job queue to use")
        argparser.add_argument("-b", action="store_true", dest="large_seg",
                               help="Use large segment support")
        argparser.add_argument("--cgsp",
                               help="Number of slave cores per core group")
        argparser.add_argument("--share_size",
                               help="Share region size")
        argparser.add_argument("--host_stack",
                               help="Host stack size")

    @classmethod
    def parse_cmdline_args(cls, namespace):
        return {"queue": namespace.queue,
                "cgsp": namespace.cgsp,
                "large_seg": namespace.large_seg,
                "share_size": namespace.share_size,
                "host_stack": namespace.host_stack}

    def __init__(self, args):
        self.args = args

    def run(self, case, timeout=None, make_script=False, dryrun=False,
            verbose=False, **kwargs):
        test_vector = case["test_vector"]
        path = case["path"]
        spec = case["spec"]
        assert os.path.isabs(path)

        run = spec["run"]
        nprocs = str(run["nprocs"])
        procs_per_node = run.get("procs_per_node", None)
        bsub_cmd = ["bsub", "-I"]
        bsub_cmd.extend(["-n", nprocs])
        if procs_per_node:
            bsub_cmd.extend(["-np", procs_per_node])
        if self.args["large_seg"]:
            bsub_cmd.append("-b")
        # TODO: add timeout support
        # if timeout:
        #     bsub_cmd.extend(["-t", str(timeout)])
        if self.args["queue"]:
            bsub_cmd.extend(["-q", self.args["queue"]])
        if self.args["cgsp"]:
            bsub_cmd.extend(["-cgsp", self.args["cgsp"]])
        if self.args["share_size"]:
            bsub_cmd.extend(["-share_size", self.args["share_size"]])
        if self.args["host_stack"]:
            bsub_cmd.extend(["-host_stack", self.args["host_stack"]])
        exec_cmd = map(str, spec["cmd"])
        cmd = bsub_cmd + exec_cmd
        cmd = map(str, cmd)

        env = dict(os.environ)
        for k, v in spec["envs"].iteritems():
            env[k] = str(v)

        if make_script:
            make_bash_script(
                cmd, spec["envs"], os.path.join(path, "run.sh"))
        if dryrun:
            return "skipped"

        out_fn = os.path.join(path, "STDOUT")
        err_fn = os.path.join(path, "STDERR")

        if verbose:
            proc1 = subprocess.Popen(cmd, env=env, cwd=path,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT)
            proc2 = subprocess.Popen(["tee", out_fn], cwd=path,
                                     stdin=proc1.stdout)
            proc1.stdout.close()
            ret = proc2.wait()
        else:
            ret = subprocess.call(cmd, env=env, cwd=path,
                                  stdout=file(out_fn, "w"),
                                  stderr=file(err_fn, "w"))

        if ret == 0:
            return "success"
        # FIXME: find the correct return code for timeout
        elif ret == 124:
            return "timeout"
        else:
            return "failed"


class SimpleProgressReporter:

    def project_begin(self, project):
        sys.stdout.write("Start project %s:\n" % project.name)
        sys.stdout.flush()
        self.total_cases = project.count_cases()
        self.finished_cases = 0

    def project_end(self, project, stats):
        sys.stdout.write("Done.\n")
        stats_str = ", ".join("%d %s" % (len(v), k)
                              for k, v in stats.iteritems()) + "\n"
        sys.stdout.write(stats_str)
        sys.stdout.flush()

    def case_begin(self, project, case):
        self.finished_cases += 1
        completed = float(self.finished_cases) / float(self.total_cases) * 100
        pretty_case = os.path.relpath(case["path"], project.project_root)
        sys.stdout.write("   [%3.0f%%] Run %s ... " % (completed, pretty_case))
        sys.stdout.flush()

    def case_end(self, project, case, result):
        sys.stdout.write("%s\n" % result)
        sys.stdout.flush()


def run_project(project, runner, reporter, timeout=None, make_script=True,
                dryrun=False, verbose=False, exclude=[], include=[],
                skip_finished=False):
    stats = OrderedDict(zip(["success", "timeout", "failed", "skipped"],
                            [[], [], [], []]))
    if skip_finished and project.last_stats:
        stats["success"] = project.last_stats["success"]

    def has_match(path, matchers):
        for m in matchers:
            if fnmatch.fnmatch(path, m):
                return True
        return False

    reporter.project_begin(project)
    for case in project.itercases():
        if skip_finished and case in stats["success"]:
            continue
        case_path = os.path.relpath(case["path"], project.project_root)
        case_id = {"test_vector": case["test_vector"], "path": case_path}
        if exclude and has_match(case_path, exclude):
            stats["skipped"].append(case_id)
            continue
        elif include and not has_match(case_path, include):
            stats["skipped"].append(case_id)
            continue
        reporter.case_begin(project, case)
        result = runner.run(case, verbose=verbose, timeout=timeout,
                            make_script=make_script, dryrun=dryrun)
        reporter.case_end(project, case, result)
        stats[result].append(case_id)
    reporter.project_end(project, stats)

    if not dryrun:
        runlog_path = os.path.join(project.project_root, "run_stats.json")
        json.dump(stats, file(runlog_path, "w"), indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    ag = parser.add_argument_group("Global options")
    ag.add_argument("project_root",
                    help="Root directory of the test project")
    ag.add_argument("--skip-finished", action="store_true",
                    help="Skip already finished cases")

    ag = parser.add_argument_group("Filter options")
    ag.add_argument("-e", "--exclude", action="append", default=[],
                    help="Excluded case paths, support shell wildcards")
    ag.add_argument("-i", "--include", action="append", default=[],
                    help="Included case paths, support shell wildcards")

    ag = parser.add_argument_group("Runner options")
    ag.add_argument("--case-runner",
                    choices=["yhrun", "bsub", "mpirun", "auto"],
                    default="auto", help="Runner to choose (default: auto)")
    ag.add_argument("-t", "--timeout", default=None,
                    help="Timeout for each case, in minites")
    ag.add_argument("--make-script", action="store_true",
                    help="Generate job script for each case")
    ag.add_argument("--dryrun", action="store_true",
                    help="Don't actually run cases")
    ag.add_argument("--verbose", action="store_true", default=False,
                    help="Be verbose (print jobs output currently)")

    ag = parser.add_argument_group("yhrun options")
    YhrunRunner.register_cmdline_args(ag)

    ag = parser.add_argument_group("mpirun options")
    MpirunRunner.register_cmdline_args(ag)

    ag = parser.add_argument_group("bsub options")
    BsubRunner.register_cmdline_args(ag)

    config = parser.parse_args()

    proj = TestProjectReader(config.project_root)
    if config.case_runner == "mpirun":
        runner = MpirunRunner(MpirunRunner.parse_cmdline_args(config))
    elif config.case_runner == "yhrun":
        runner = YhrunRunner(YhrunRunner.parse_cmdline_args(config))
    elif config.case_runner == "bsub":
        runner = BsubRunner(BsubRunner.parse_cmdline_args(config))
    else:
        # automatically determine which is the best
        if YhrunRunner.is_available():
            runner = YhrunRunner(YhrunRunner.parse_cmdline_args(config))
        elif BsubRunner.is_available():
            runner = BsubRunner(BsubRunner.parse_cmdline_args(config))
        elif MpirunRunner.is_available():
            runner = MpirunRunner(MpirunRunner.parse_cmdline_args(config))
        else:
            raise RuntimeError(
                "Failed to automatically determine runner, please specify "
                "one via --case-runner")

    run_project(proj, runner, SimpleProgressReporter(), timeout=config.timeout,
                make_script=config.make_script, dryrun=config.dryrun,
                verbose=config.verbose, exclude=config.exclude,
                include=config.include, skip_finished=config.skip_finished)


if __name__ == "__main__":
    main()