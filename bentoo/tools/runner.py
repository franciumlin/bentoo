# coding: utf-8
#
''' bentoo-runner - Testcase runner

bentoo-runner runs the test cases generated by bentoo-generator on different
high performance computing platforms.  It features test case filters, results
validator, timeout etc. It supports slurm, pbs, yhrun (tianhe), bsub (sunway),
and plain mpirun at the moment. More backends will be added overtime.
'''
from __future__ import division, print_function, unicode_literals

import argparse
import fnmatch
import json
import os
import re
import string
import subprocess
import sys
import time
from collections import OrderedDict

from bentoo.common.project import TestProjectReader
from bentoo.common.utils import has_program, make_bash_script, shell_quote

#
# Interfaces of a job launcher:
#
# class XxxLauncher(object):
#     # Check if the laucher is possible on current system
#     @classmethod
#     def is_available(cls): pass
#
#     # Register extra cmdline arguments, shall prefix each argument with xxx,
#     # so argument `foo` shall be `--xxx-foo`.
#     @classmethod
#     def register_cmdline_args(cls, argparser): pass
#
#     # Accept values from parsed arguments
#     def parse_cmdline_args(cls, namespace): pass
#
#     # Run the actural case, return the status. When timeout is set, the case
#     shall not run longger than that. When make_script is set, a bash script to
#     run the job manully shall be created under case dir. When dryrun is set,
#     every operation other than the actural run shall finish, but the actural
#     run shall be skipped. When verbose is set, the job's stdout and stderr
#     shall be redirected to the tty. Other arguments specific to the laucher
#     can be passed through kwargs. The return status can be `success`, `failed`
#     or `timeout`.
#     def run(self, case, timeout=None, make_script=False,
#             dryrun=False, verbose=False, **kwargs): pass
#


class MpirunLauncher(object):
    '''Job launcher for mpirun (plain mpi)'''
    @classmethod
    def is_available(cls):
        if has_program(["mpirun", "-h"]):
            return True
        elif has_program(["mpiexec", "-h"]):
            return True
        return False

    @classmethod
    def register_cmdline_args(cls, argparser):
        argparser.add_argument("--mpirun-hosts",
                               default=None,
                               metavar="HOSTS",
                               dest="mpirun_hosts",
                               help="Comma seperated host list")
        argparser.add_argument("--mpirun-ppn",
                               default=None,
                               metavar="PPN",
                               dest="mpirun_ppn",
                               help="Processes per node")

    @classmethod
    def parse_cmdline_args(cls, namespace):
        return {"hosts": namespace.mpirun_hosts, "ppn": namespace.mpirun_ppn}

    def __init__(self, args):
        self.args = args

    def run(self,
            case,
            timeout=None,
            make_script=False,
            dryrun=False,
            verbose=False,
            **kwargs):
        path = case["fullpath"]
        spec = case["spec"]
        assert os.path.isabs(path)

        nprocs = str(spec["run"]["nprocs"])
        mpirun_cmd = ["mpirun", "-np", nprocs]
        if self.args["hosts"]:
            mpirun_cmd.extend(["-hosts", self.args["hosts"]])
        if self.args["ppn"]:
            mpirun_cmd.extend(["-ppn", self.args["ppn"]])
        exec_cmd = list(map(str, spec["cmd"]))
        cmd = mpirun_cmd + exec_cmd
        if timeout:
            cmd = ["timeout", "{0}m".format(timeout)] + cmd

        env = dict(os.environ)
        for k, v in spec["envs"].items():
            env[k] = str(v)

        if make_script:
            make_bash_script(None, spec["envs"], [cmd],
                             os.path.join(path, "run.sh"))

        if dryrun:
            return None

        out_fn = os.path.join(path, "STDOUT")
        err_fn = os.path.join(path, "STDERR")

        if verbose:
            proc1 = subprocess.Popen(cmd,
                                     env=env,
                                     cwd=path,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT)
            proc2 = subprocess.Popen(["tee", out_fn],
                                     cwd=path,
                                     stdin=proc1.stdout)
            proc1.stdout.close()
            ret = proc2.wait()
        else:
            ret = subprocess.call(cmd,
                                  env=env,
                                  cwd=path,
                                  stdout=open(out_fn, "w"),
                                  stderr=open(err_fn, "w"))

        if ret == 0:
            return "success"
        elif ret == 124:
            return "timeout"
        return "failed"


class YhrunLauncher(object):
    '''Job launcher for yhrun (slurm variants on tianhe)'''
    @classmethod
    def is_available(cls):
        if has_program(["yhrun", "-h"]):
            return True
        return False

    @classmethod
    def register_cmdline_args(cls, argparser):
        argparser.add_argument("--yhrun-p",
                               "--yhrun-partition",
                               metavar="PARTITION",
                               dest="yhrun_partition",
                               help="Select job partition to use")
        argparser.add_argument("--yhrun-x",
                               metavar="NODELIST",
                               dest="yhrun_excluded_nodes",
                               help="Exclude nodes from job allocation")
        argparser.add_argument("--yhrun-w",
                               metavar="NODELIST",
                               dest="yhrun_only_nodes",
                               help="Use only selected nodes")
        argparser.add_argument("--yhrun-yhbatch",
                               action="store_true",
                               dest="yhrun_yhbatch",
                               help="Use yhbatch instead of yhrun")
        argparser.add_argument("--yhrun-fix-glex",
                               choices=("none", "v0", "v1", "v2"),
                               default="none",
                               dest="yhrun_fix_glex",
                               help="Fix GLEX settings (default: none)")
        argparser.add_argument(
            "--yhrun-yhbcast",
            action="store_true",
            dest="yhrun_yhbcast",
            help="Use yhbcast to prepare a node-local directory")

    @classmethod
    def parse_cmdline_args(cls, namespace):
        return {
            "partition": namespace.yhrun_partition,
            "excluded_nodes": namespace.yhrun_excluded_nodes,
            "only_nodes": namespace.yhrun_only_nodes,
            "use_batch": namespace.yhrun_yhbatch,
            "fix_glex": namespace.yhrun_fix_glex,
            "use_yhbcast": namespace.yhrun_yhbcast,
        }

    def __init__(self, args):
        self.args = args

    def run(self,
            case,
            timeout=None,
            make_script=False,
            dryrun=False,
            verbose=False,
            **kwargs):
        path = case["fullpath"]
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
        yhrun_cmd.extend(["-o", "STDOUT", "-e", "STDERR"])
        exec_cmd = list(map(str, spec["cmd"]))
        cmd = yhrun_cmd + exec_cmd
        cmd = list(map(str, cmd))

        env = {}
        for k, v in spec["envs"].items():
            env[k] = str(v)

        if self.args["fix_glex"] == "v0":
            if int(nprocs) > 8192:
                env["PDP_GLEX_USE_HC_MPQ"] = "1"
                env["PDP_GLEX_HC_MPQ_L1_CAPACITY"] = "16384"
                env["GLEX_BYPASS_RDMA_WRITE_CHANNEL"] = "1"
                env["GLEX_EP_MPQ_SLOTS"] = "131072"
                env["GLEX_USE_ZC_RNDV"] = "0"
        elif self.args["fix_glex"] == "v1":
            if int(nprocs) > 8192:
                env["MPICH_NO_LOCAL"] = "1"
                env["GLEX_BYPASS_ER"] = "1"
                env["GLEX_USE_ZC_RNDV"] = "0"
        elif self.args["fix_glex"] == "v2":
            ppn = run.get("procs_per_node", 1)
            if int(ppn) > 32:
                env["MPICH_NEMESIS_NETMOD"] = "tcp"
            if int(nnodes) > 1:
                env["MPICH_CH3_NO_LOCAL"] = "1"

        # Force use_batch if yhbcast is required.
        if self.args["use_yhbcast"]:
            self.args["use_batch"] = True

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
            script_cmds = [real_cmd]
            if self.args["use_yhbcast"] and "mirror_files" in spec:
                bcast_cmds = []
                cleanup_cmds = []
                for k, v in spec["mirror_files"].items():
                    bcast_cmds.append(["yhbcast", k, v])
                    cleanup_cmds.append(yhrun_cmd + ["rm", "-f", v])
                script_cmds = cleanup_cmds + bcast_cmds
                script_cmds += [real_cmd] + cleanup_cmds
            make_bash_script(None, env, script_cmds,
                             os.path.join(path, "batch_spec.sh"))
            # build yhbatch command line
            yhbatch_cmd = ["yhbatch", "-N", str(nnodes)]
            if self.args["partition"]:
                yhbatch_cmd.extend(["-p", self.args["partition"]])
            if self.args["excluded_nodes"]:
                yhbatch_cmd.extend(["-x", self.args["excluded_nodes"]])
            if self.args["only_nodes"]:
                yhbatch_cmd.extend(["-w", self.args["only_nodes"]])
            yhbatch_cmd.extend(["-J", os.path.basename(exec_cmd[0])])
            yhbatch_cmd.append("./batch_spec.sh")

            if make_script:
                make_bash_script(None, None, [yhbatch_cmd],
                                 os.path.join(path, "run.sh"))
            if dryrun:
                return None

            subprocess.call(yhbatch_cmd, cwd=path)
            # yhbatch always success
            return "success"

        else:
            if make_script:
                make_bash_script(None, env, [cmd],
                                 os.path.join(path, "run.sh"))
            if dryrun:
                return None

            out_fn = os.path.join(path, "STDOUT")
            err_fn = os.path.join(path, "STDERR")

            env.update(os.environ)
            if verbose:
                proc1 = subprocess.Popen(cmd,
                                         env=env,
                                         cwd=path,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT)
                proc2 = subprocess.Popen(["tee", out_fn],
                                         cwd=path,
                                         stdin=proc1.stdout)
                proc1.stdout.close()
                ret = proc2.wait()
            else:
                ret = subprocess.call(cmd,
                                      env=env,
                                      cwd=path,
                                      stdout=open(out_fn, "w"),
                                      stderr=open(err_fn, "w"))

            if ret == 0:
                return "success"
            # FIXME: find the correct return code for timeout
            elif ret == 124:
                return "timeout"
            return "failed"


class MpiHandler(object):
    '''Vendor MPI handler'''
    def __init__(self, vendor="mpich"):
        self.vendor = vendor

    def build_job_argument_handler(self,
                                   nprocs,
                                   procs_per_node,
                                   hostfile,
                                   timeout=None):
        '''Build command line and environments for job control system requests

        Arguments:
            nprocs: int, total number of procs for the job
            procs_per_node: int, number of procs per node in the host file
            hostfile: file path, hostfile generated by the job system
            timeout: int, timeout for the job, in minites.

        Returns:
            dict like `{'cmd': ['mpirun', '-n', 1], 'envs': {}}`
        '''
        if self.vendor == "mpich":
            return {
                "cmd": [
                    "mpirun", "-n",
                    str(nprocs), "-ppn",
                    str(procs_per_node), "-hosts",
                    str(hostfile)
                ],
                "envs": {
                    "MPIEXEC_TIMEOUT": str(int(timeout) * 60)
                } if timeout else {}
            }
        elif self.vendor == "openmpi":
            return {
                "cmd": [
                    "mpirun", "-n",
                    str(nprocs), "--map-by", "slot", "-hostfile",
                    str(hostfile)
                ],
                "envs": {
                    "MPIEXEC_TIMEOUT": str(int(timeout) * 60)
                } if timeout else {}
            }
        elif self.vendor == "mvapich2":
            return {
                "cmd": [
                    "mpirun", "-n",
                    str(nprocs), "-ppn",
                    str(procs_per_node), "-hosts",
                    str(hostfile)
                ],
                "envs": {
                    "MPIEXEC_TIMEOUT": str(int(timeout) * 60)
                } if timeout else {}
            }
        elif self.vendor == "intelmpi":
            return {
                "cmd": [
                    "mpirun", "-n",
                    str(nprocs), "-ppn",
                    str(procs_per_node), "-hosts",
                    str(hostfile)
                ],
                "envs": {
                    "MPIEXEC_TIMEOUT": str(int(timeout) * 60)
                } if timeout else {}
            }
        else:
            raise ValueError("Unsupported mpi: %s" % self.vendor)


class SlurmLauncher(object):
    '''Job launcher for general slurm'''
    @classmethod
    def is_available(cls):
        if has_program(["sbatch", "-h"]):
            return True
        return False

    @classmethod
    def register_cmdline_args(cls, argparser):
        argparser.add_argument("--slurm-partition",
                               metavar="PARTITION",
                               dest="slurm_partition",
                               help="Select job partition to use")
        argparser.add_argument("--slurm-sbatch",
                               action="store_true",
                               dest="slrum_use_batch",
                               help="Use sbatch instead of srun")
        argparser.add_argument("--slurm-mpi",
                               metavar="MPI",
                               dest="slurm_mpi",
                               choices=("openmpi", "mpich", "mvapich",
                                        "intelmpi"),
                               default="openmpi",
                               help="Select the MPI to use (default: openmpi)")

    @classmethod
    def parse_cmdline_args(cls, namespace):
        return {
            "partition": namespace.slurm_partition,
            "use_batch": namespace.slurm_use_batch,
            "mpi": namespace.slurm_mpi,
        }

    def __init__(self, args):
        self.args = args

    def run(self,
            case,
            timeout=None,
            make_script=False,
            dryrun=False,
            verbose=False,
            **kwargs):
        path = case["fullpath"]
        spec = case["spec"]
        assert os.path.isabs(path)

        run = spec["run"]
        nprocs = str(run["nprocs"])
        nnodes = run.get("nnodes", None)
        procs_per_node = run.get("procs_per_node", None)
        tasks_per_proc = run.get("tasks_per_proc", None)
        srun_cmd = ["srun"]
        if nnodes:
            srun_cmd.extend(["-N", nnodes])
        srun_cmd.extend(["-n", nprocs])
        if procs_per_node:
            srun_cmd.extend(["--ntasks-per-node", procs_per_node])
        if tasks_per_proc:
            srun_cmd.extend(["-c", tasks_per_proc])
        if timeout:
            srun_cmd.extend(["-t", str(timeout)])
        if self.args["partition"]:
            srun_cmd.extend(["-p", self.args["partition"]])
        exec_cmd = list(map(str, spec["cmd"]))
        cmd = srun_cmd + exec_cmd
        cmd = list(map(str, cmd))

        env = dict(os.environ)
        for k, v in spec["envs"].items():
            env[k] = str(v)

        if self.args["use_batch"]:
            # build sbatch job spec file
            prolog = []
            prolog.append("SBATCH -J {}".format(os.path.basename(exec_cmd[0])))
            if nnodes:
                prolog.append("SBATCH -N {}".format(nnodes))
            prolog.append("SBATCH -n {}".format(nprocs))
            if procs_per_node:
                prolog.append(
                    "SBATCH --ntasks-per-node {}".format(procs_per_node))
            if tasks_per_proc:
                prolog.append("SBATCH -c {}".format(tasks_per_proc))
            if timeout:
                prolog.append("SBATCH -t {}".format(timeout))
            if self.args["partition"]:
                prolog.append("SBATCH -p {}".format(self.args["partition"]))
            prolog.append("SBATCH -o STDOUT")
            prolog.append("SBATCH -e STDERR")
            jobcmds = []
            jobcmds.append(srun_cmd + ["hostname"] +
                           "> /tmp/hostfile-$$".split())
            mpi_handler = MpiHandler(
                self.args['mpi']).build_job_argument_handler(
                    nprocs, 1, "/tmp/hostfile-$$", None)
            jobcmds.append(mpi_handler['cmd'] + exec_cmd)
            jobcmds.append("rm -f /tmp/hostfile-$$".split())

            envs = dict(spec["envs"])
            envs.update(mpi_handler["envs"])
            make_bash_script(prolog, envs, jobcmds,
                             os.path.join(path, "job_spec.sh"))

            sbatch_cmd = ["sbatch", "job_spec.sh"]
            if make_script:
                make_bash_script(None, None, [sbatch_cmd],
                                 os.path.join(path, "run.sh"))
            if dryrun:
                return None

            subprocess.call(sbatch_cmd, cwd=path)
            # sbatch always success
            return "success"

        else:
            if make_script:
                make_bash_script(None, spec["envs"], [cmd],
                                 os.path.join(path, "run.sh"))
            if dryrun:
                return None

            out_fn = os.path.join(path, "STDOUT")
            err_fn = os.path.join(path, "STDERR")

            if verbose:
                proc1 = subprocess.Popen(cmd,
                                         env=env,
                                         cwd=path,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT)
                proc2 = subprocess.Popen(["tee", out_fn],
                                         cwd=path,
                                         stdin=proc1.stdout)
                proc1.stdout.close()
                ret = proc2.wait()
            else:
                ret = subprocess.call(cmd,
                                      env=env,
                                      cwd=path,
                                      stdout=open(out_fn, "w"),
                                      stderr=open(err_fn, "w"))

            if ret == 0:
                return "success"
            # FIXME: find the correct return code for timeout
            elif ret == 124:
                return "timeout"
            else:
                return "failed"


PBS_TEMPLATE = '''#PBS -N ${jobname}
#PBS -l nodes=${nnodes}:ppn=${ppn}
#PBS -j oe
#PBS -n
#PBS -V
#PBS -o STDOUT
${queue}
${timeout}

${envs}

cd $PBS_O_WORKDIR
mpirun -np ${nprocs} -ppn ${procs_per_node} -machinefile \
    $PBS_NODEFILE ${iface} ${cmd}
'''


class PbsLauncher(object):
    '''Job launcher for PBS'''
    @classmethod
    def is_available(cls):
        if has_program(["qstat", "-h"]):
            return True
        else:
            return False

    @classmethod
    def register_cmdline_args(cls, argparser):
        argparser.add_argument("--pbs-queue",
                               metavar="QUEUE",
                               dest="pbs_queue",
                               help="Select job queue to use")
        argparser.add_argument("--pbs-iface",
                               metavar="IFACE",
                               dest="pbs_iface",
                               help="Network interface to use")

    @classmethod
    def parse_cmdline_args(cls, namespace):
        return {"queue": namespace.pbs_queue, "iface": namespace.pbs_iface}

    def __init__(self, args):
        self.args = args

    def run(self,
            case,
            timeout=None,
            make_script=False,
            dryrun=False,
            verbose=False,
            **kwargs):
        path = case["fullpath"]
        spec = case["spec"]
        assert os.path.isabs(path)

        run = spec["run"]
        nnodes = str(run["nnodes"])
        procs_per_node = str(run["procs_per_node"])
        nprocs = str(run["nprocs"])
        exec_cmd = " ".join(["\"{0}\"".format(x) for x in spec["cmd"]])

        tplvars = {
            "nprocs": nprocs,
            "procs_per_node": procs_per_node,
            "nnodes": nnodes,
            "cwd": path,
            "cmd": exec_cmd,
            "iface": "",
            "queue": "",
            "jobname": os.path.basename(spec["cmd"][0]),
            "ppn": 1,
            "timeout": "",
        }
        if self.args["iface"]:
            tplvars["iface"] = "-iface {}".format(self.args["iface"])
        if self.args["queue"]:
            tplvars["queue"] = "#PBS -q {}".format(self.args["queue"])
        if timeout:
            timeout = int(timeout)
            tplvars["timeout"] = "#PBS -l walltime={0:02d}:{1:02d}:00".format(
                timeout // 60, timeout % 60)

        envs_str = []
        for k, v in spec["envs"].items():
            envs_str.append("export {0}={1}".format(k, shell_quote(v)))
        envs_str = "\n".join(envs_str)
        tplvars["envs"] = envs_str

        pbs_file = os.path.join(path, "job_spec.pbs")
        tpl = string.Template(PBS_TEMPLATE)
        open(pbs_file, "w").write(tpl.safe_substitute(tplvars))

        if make_script:
            script_file = os.path.join(path, "run.sh")
            pbscmd = ['qsub', './job_spec.pbs']
            make_bash_script(None, None, [pbscmd], script_file)

        if dryrun:
            return None

        env = dict(os.environ)
        for k, v in spec["envs"].items():
            env[k] = str(v)
        cmd = ["qsub", "./job_spec.pbs"]
        ret = subprocess.call(cmd, env=env, cwd=path, shell=False)

        if ret == 0:
            return "success"
        # FIXME: find the correct return code for timeout
        elif ret == 124:
            return "timeout"
        return "failed"


class BsubLauncher(object):
    '''Job launcher for Sunway TaihuLight'''
    @classmethod
    def is_available(cls):
        if has_program(["bsub", "-h"]):
            return True
        return False

    @classmethod
    def register_cmdline_args(cls, argparser):
        argparser.add_argument("--bsub-queue",
                               metavar="QUEUE",
                               dest="bsub_queue",
                               help="Select job queue to use")
        argparser.add_argument("--bsub-b",
                               action="store_true",
                               dest="bsub_large_seg",
                               help="Use large segment support")
        argparser.add_argument("--bsub-cgsp",
                               metavar="CGSP",
                               dest="bsub_cgsp",
                               help="Number of slave cores per core group")
        argparser.add_argument("--bsub-share_size",
                               metavar="SIZE",
                               dest="bsub_share_size",
                               help="Share region size")
        argparser.add_argument("--bsub-host_stack",
                               metavar="SIZE",
                               dest="bsub_host_stack",
                               help="Host stack size")

    @classmethod
    def parse_cmdline_args(cls, namespace):
        return {
            "queue": namespace.bsub_queue,
            "cgsp": namespace.bsub_cgsp,
            "large_seg": namespace.bsub_large_seg,
            "share_size": namespace.bsub_share_size,
            "host_stack": namespace.bsub_host_stack
        }

    def __init__(self, args):
        self.args = args

    def run(self,
            case,
            timeout=None,
            make_script=False,
            dryrun=False,
            verbose=False,
            **kwargs):
        path = case["fullpath"]
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
        exec_cmd = list(map(str, spec["cmd"]))
        cmd = bsub_cmd + exec_cmd
        cmd = list(map(str, cmd))

        env = dict(os.environ)
        for k, v in spec["envs"].items():
            env[k] = str(v)

        if make_script:
            make_bash_script(None, spec["envs"], [cmd],
                             os.path.join(path, "run.sh"))
        if dryrun:
            return None

        out_fn = os.path.join(path, "STDOUT")
        err_fn = os.path.join(path, "STDERR")

        if verbose:
            proc1 = subprocess.Popen(cmd,
                                     env=env,
                                     cwd=path,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT)
            proc2 = subprocess.Popen(["tee", out_fn],
                                     cwd=path,
                                     stdin=proc1.stdout)
            proc1.stdout.close()
            ret = proc2.wait()
        else:
            ret = subprocess.call(cmd,
                                  env=env,
                                  cwd=path,
                                  stdout=open(out_fn, "w"),
                                  stderr=open(err_fn, "w"))

        if ret == 0:
            return "success"
        # FIXME: find the correct return code for timeout
        elif ret == 124:
            return "timeout"
        return "failed"


class SimpleProgressReporter(object):
    '''A simple progress reporter'''
    def __init__(self):
        self.total_cases = 0
        self.finished_cases = 0

    def project_begin(self, project):
        '''Notify the start of the test project'''
        sys.stdout.write("Start project %s:\n" % project.name)
        sys.stdout.flush()
        self.total_cases = project.count_cases()
        self.finished_cases = 0

    def project_end(self, project, stats):
        '''Notify the end of the test project'''
        sys.stdout.write("Done.\n")
        stats_str = ", ".join("%d %s" % (len(v), k)
                              for k, v in stats.items()) + "\n"
        sys.stdout.write(stats_str)
        sys.stdout.flush()

    def case_begin(self, project, case):
        '''Notify the start of a test case run'''
        self.finished_cases += 1
        completed = float(self.finished_cases) / float(self.total_cases) * 100
        pretty_case = os.path.relpath(case["fullpath"], project.project_root)
        sys.stdout.write("   [%3.0f%%] Run %s ... " % (completed, pretty_case))
        sys.stdout.flush()

    def case_end(self, project, case, result):
        '''Notify the result of a test case run'''
        sys.stdout.write("%s\n" % result)
        sys.stdout.flush()


def validate_case(case):
    '''Check if a test case finished successfully'''
    # No 'validate' method means always valid
    if "validator" not in case["spec"]:
        return True
    validator = case["spec"]["validator"]
    if "exists" in validator:
        for f in validator["exists"]:
            fullpath = os.path.join(case["fullpath"], f)
            if not os.path.exists(fullpath):
                return False
    if "contains" in validator:
        for k, v in validator["contains"].items():
            fullpath = os.path.join(case["fullpath"], k)
            if not os.path.exists(fullpath):
                return False
            if not re.search(v, open(fullpath).read()):
                return False
    return True


def run_project(project,
                runner,
                reporter,
                timeout=None,
                make_script=True,
                dryrun=False,
                verbose=False,
                exclude=[],
                include=[],
                skip_finished=False,
                sleep=0,
                rerun_failed=False):
    '''Run a test project'''
    stats = OrderedDict(
        list(zip(["success", "timeout", "failed", "skipped"],
                 [[], [], [], []])))
    if skip_finished and project.last_stats:
        stats["success"] = project.last_stats["success"]

    def has_match(path, matchers):
        for m in matchers:
            if fnmatch.fnmatch(path, m):
                return True
        return False

    reporter.project_begin(project)
    for case in project.itercases():
        case_path = os.path.relpath(case["fullpath"], project.project_root)
        case_id = {"test_vector": case["test_vector"], "path": case_path}
        if exclude and has_match(case_path, exclude):
            stats["skipped"].append(case_id)
            reporter.case_begin(project, case)
            reporter.case_end(project, case, "skipped since excluded")
            continue
        elif include and not has_match(case_path, include):
            stats["skipped"].append(case_id)
            reporter.case_begin(project, case)
            reporter.case_end(project, case, "skipped since not included")
            continue
        if rerun_failed and validate_case(case):
            reporter.case_begin(project, case)
            reporter.case_end(project, case, "skipped since done")
            continue
        if skip_finished and case in stats["success"]:
            reporter.case_begin(project, case)
            reporter.case_end(project, case, "skipped since in success")
            continue
        reporter.case_begin(project, case)
        result = runner.run(case,
                            verbose=verbose,
                            timeout=timeout,
                            make_script=make_script,
                            dryrun=dryrun)
        reporter.case_end(project, case, "dryrun" if dryrun else result)
        if result:
            stats[result].append(case_id)
        if sleep:
            time.sleep(sleep)
    reporter.project_end(project, stats)

    if not dryrun:
        runlog_path = os.path.join(project.project_root, "run_stats.json")
        json.dump(stats, open(runlog_path, "w"), indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    ag = parser.add_argument_group("Global options")
    ag.add_argument("project_root", help="Root directory of the test project")
    ag.add_argument("--skip-finished",
                    action="store_true",
                    help="Skip already finished cases")
    ag.add_argument("--rerun-failed",
                    action="store_true",
                    help="Rerun failed jobs (using validator to determine)")

    ag = parser.add_argument_group("Filter options")
    ag.add_argument("-e",
                    "--exclude",
                    action="append",
                    default=[],
                    help="Excluded case paths, support shell wildcards")
    ag.add_argument("-i",
                    "--include",
                    action="append",
                    default=[],
                    help="Included case paths, support shell wildcards")

    ag = parser.add_argument_group("Launcher options")
    ag.add_argument(
        "--launcher",
        choices=["yhrun", "bsub", "slurm", "pbs", "mpirun", "auto"],
        default="auto",
        help="Job launcher (default: auto)")
    ag.add_argument("-t",
                    "--timeout",
                    default=None,
                    help="Timeout for each case, in minites")
    ag.add_argument("--sleep",
                    type=int,
                    default=0,
                    help="Sleep specified seconds between jobs")
    ag.add_argument("--make-script",
                    action="store_true",
                    help="Generate job script for each case")
    ag.add_argument("--dryrun",
                    action="store_true",
                    help="Don't actually run cases")
    ag.add_argument("--verbose",
                    action="store_true",
                    default=False,
                    help="Be verbose (print jobs output currently)")

    ag = parser.add_argument_group("yhrun options")
    YhrunLauncher.register_cmdline_args(ag)

    ag = parser.add_argument_group("bsub options")
    BsubLauncher.register_cmdline_args(ag)

    ag = parser.add_argument_group("slurm options")
    SlurmLauncher.register_cmdline_args(ag)

    ag = parser.add_argument_group("pbs options")
    PbsLauncher.register_cmdline_args(ag)

    ag = parser.add_argument_group("mpirun options")
    MpirunLauncher.register_cmdline_args(ag)

    config = parser.parse_args()

    proj = TestProjectReader(config.project_root)
    if config.launcher == "mpirun":
        runner = MpirunLauncher(MpirunLauncher.parse_cmdline_args(config))
    elif config.launcher == "yhrun":
        runner = YhrunLauncher(YhrunLauncher.parse_cmdline_args(config))
    elif config.launcher == "pbs":
        runner = PbsLauncher(PbsLauncher.parse_cmdline_args(config))
    elif config.launcher == "bsub":
        runner = BsubLauncher(BsubLauncher.parse_cmdline_args(config))
    elif config.launcher == "slurm":
        runner = SlurmLauncher(SlurmLauncher.parse_cmdline_args(config))
    else:
        # automatically determine which is the best
        if YhrunLauncher.is_available():
            runner = YhrunLauncher(YhrunLauncher.parse_cmdline_args(config))
        elif BsubLauncher.is_available():
            runner = BsubLauncher(BsubLauncher.parse_cmdline_args(config))
        elif SlurmLauncher.is_available():
            runner = SlurmLauncher(SlurmLauncher.parse_cmdline_args(config))
        elif PbsLauncher.is_available():
            runner = PbsLauncher(PbsLauncher.parse_cmdline_args(config))
        elif MpirunLauncher.is_available():
            runner = MpirunLauncher(MpirunLauncher.parse_cmdline_args(config))
        else:
            raise RuntimeError(
                "Failed to automatically determine launcher, please specify "
                "one via --launcher")

    run_project(proj,
                runner,
                SimpleProgressReporter(),
                timeout=config.timeout,
                make_script=config.make_script,
                dryrun=config.dryrun,
                verbose=config.verbose,
                exclude=config.exclude,
                include=config.include,
                skip_finished=config.skip_finished,
                sleep=config.sleep,
                rerun_failed=config.rerun_failed)


if __name__ == "__main__":
    main()
