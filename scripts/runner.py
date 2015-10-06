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


class MpirunRunner:

    @classmethod
    def register_cmdline_args(cls, argparser):
        pass

    @classmethod
    def parse_cmdline_args(cls, namespace):
        return {}

    def __init__(self, args):
        self.args = args

    def run(self, case, timeout=None, **kwargs):
        test_vector = case["test_vector"]
        path = case["path"]
        spec = case["spec"]
        assert os.path.isabs(path)

        nprocs = str(spec["run"]["nprocs"])
        mpirun_cmd = ["mpirun", "-np", nprocs]
        exec_cmd = map(str, spec["cmd"])
        cmd = mpirun_cmd + exec_cmd
        if timeout:
            cmd = ["timeout", "{0}m".format(timeout)] + cmd

        env = dict(os.environ)
        for k, v in spec["envs"].iteritems():
            env[k] = str(v)
        out_fn = os.path.join(path, "STDOUT")
        err_fn = os.path.join(path, "STDERR")

        ret = subprocess.call(cmd,
                              env=env,
                              cwd=path,
                              stdout=file(out_fn, "w"),
                              stderr=file(err_fn, "w"))

        if ret == 0:
            return "success"
        elif ret_code == 124:
            return "timeout"
        else:
            return "failed"


class YhrunRunner:

    @classmethod
    def register_cmdline_args(cls, argparser):
        argparser.add_argument("-p, --partition",
                               metavar="PARTITION", dest="partition",
                               help="Select job partition to use")

    @classmethod
    def parse_cmdline_args(cls, namespace):
        return {"partition": namespace.partition}

    def __init__(self, args):
        self.args = args

    def run(self, case, timeout=None):
        test_vector = case["test_vector"]
        path = case["case_path"]
        spec = case["run_spec"]
        assert os.path.isabs(path)

        run = spec["run"]
        nprocs = str(run["nprocs"])
        nnodes = run.value("nnodes", None)
        tasks_per_proc = run.value("tasks_per_proc", None)
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
        cmd = yhrun_cmd + exec_cmd

        env = dict(os.environ)
        for k, v in spec["envs"].iteritems():
            env[k] = str(v)
        out_fn = os.path.join(path, "STDOUT")
        err_fn = os.path.join(path, "STDERR")

        ret = subprocess.call(cmd,
                              env=env,
                              cwd=path,
                              stdout=file(out_fn, "w"),
                              stderr=file(err_fn, "w"))

        if ret == 0:
            return "success"
        elif ret_code == 124:
            return "timeout"
        else:
            return "failed"


class SimpleProgressReporter:

    def project_begin(self, project):
        sys.stdout.write("Start project %s:\n" % project.name)
        self.total_cases = project.count_cases()
        self.finished_cases = 0

    def project_end(self, project, stats):
        sys.stdout.write("Done.\n")
        sys.stdout.write("%d success, %d failed, %d timeout.\n" % (
            len(stats["success"]), len(stats["failed"]),
            len(stats["timeout"])))

    def case_begin(self, project, case):
        self.finished_cases += 1
        completed = float(self.finished_cases) / float(self.total_cases) * 100
        pretty_case = os.path.relpath(case["path"], project.project_root)
        sys.stdout.write("   [%3.0f%%] Run %s ... " % (completed, pretty_case))

    def case_end(self, project, case, result):
        sys.stdout.write("%s\n" % result)
        sys.stdout.flush()


def run_project(project, runner, reporter):
    stats = OrderedDict(zip(["success", "timeout", "failed"], [[], [], []]))

    reporter.project_begin(project)
    for case in project.itercases():
        reporter.case_begin(project, case)
        result = runner.run(case)
        reporter.case_end(project, case, result)
        stats[result].append(case)
    reporter.project_end(project, stats)

    runlog_path = os.path.join(project.project_root, "run_stats.json")
    json.dump(stats, file(runlog_path, "w"), indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    ag = parser.add_argument_group("Global options")
    ag.add_argument("project_root",
                    help="Root directory of the test project")

    ag = parser.add_argument_group("Filter options")
    ag.add_argument("--exclude",
                    help="Test cases to exclude, support wildcards")
    ag.add_argument("--include",
                    help="Test cases to include, support wildcards")

    ag = parser.add_argument_group("Runner options")
    ag.add_argument("--case-runner",
                    choices=["mpirun", "yhrun"],
                    default="mpirun",
                    help="Runner to choose, default to mpirun")
    ag.add_argument("--timeout",
                    default=5,
                    help="Timeout for each case, in minites")

    ag = parser.add_argument_group("yhrun options")
    YhrunRunner.register_cmdline_args(ag)

    ag = parser.add_argument_group("mpirun options")
    MpirunRunner.register_cmdline_args(ag)

    config = parser.parse_args()

    proj = TestProjectReader(config.project_root)
    if config.case_runner == "mpirun":
        runner = MpirunRunner(MpirunRunner.parse_cmdline_args(config))
    elif config.case_runner == "yhrun":
        runner = YhrunRunner(YhrunRunner.parse_cmdline_args(config))
    else:
        raise NotImplementedError("This is not possible")

    run_project(proj, runner, SimpleProgressReporter())


if __name__ == "__main__":
    main()