#!/usr/bin/env python
# -*- coding: utf-8 -*-
#

from __future__ import print_function
import argparse
import os
import re
import string
import textwrap

TEXT_WIDTH = 78
SEP_LINE = "-" * TEXT_WIDTH


def wrap_print(msg):
    print(textwrap.fill(msg, width=TEXT_WIDTH))


def wrap_input(msg):
    return input(
        textwrap.fill(
            msg, width=TEXT_WIDTH, drop_whitespace=False))


def get_choice(message, choices, default=0):
    wrap_print(message + ":")
    for i, c in enumerate(choices):
        print("  [%d] %s" % (i, c))
    min_choice = 0
    max_choice = len(choices) - 1
    while True:
        choice = wrap_input("Please choose %d-%d (default: [%d]): " %
                            (min_choice, max_choice, default))
        if not choice:
            print(SEP_LINE)
            return default
        if not re.match(r"\d+", choice):
            continue
        choice = int(choice)
        if choice < 0 or choice >= len(choices):
            continue
        print(SEP_LINE)
        return choice


def get_confirm(message, default=True):
    while True:
        result = wrap_input("%s? (y/Y/n/N) (default: %s): " %
                            (message, "y" if default else "n"))
        if not result:
            print(SEP_LINE)
            return default
        if result not in {"y", "Y", "n", "N"}:
            continue
        print(SEP_LINE)
        if result.lower() == "y":
            return True
        else:
            return False


def show_message(message):
    wrap_print(message)
    print(SEP_LINE)


def get_list(message, default="nnodes"):
    while True:
        result = wrap_input("%s? (list) (default: '%s'): " %
                            (message, default))
        print(SEP_LINE)
        if not result:
            result = default
        return [x.strip() for x in result.split(",")]


def get_input(message):
    while True:
        result = wrap_input("%s: " % message)
        if not result:
            continue
        print(SEP_LINE)
        return result


def make_directories(project_dir):
    if not os.path.exists(project_dir):
        os.makedirs(project_dir)
    bin_dir = os.path.join(project_dir, "bin")
    if not os.path.exists(bin_dir):
        os.makedirs(bin_dir)


TEST_PROJECT_CONFIG_JSON_TPL = '''{
    "version": 1,
    "project": {
        "name": "${project_desc}",
        "test_factors": [${test_factors}],
        "test_vector_generator": "${vector_gen}",
        "test_case_generator": "${case_gen}",
        "data_files": ["bin"]
    },
    ${vector_generator_config},
    ${case_generator_config}
}
'''


def make_config(project_dir, project_desc, binary_name, test_factors,
                vector_gen, case_gen):
    test_factors_repr = ", ".join(["\"%s\"" % x for x in test_factors])
    if vector_gen == "cart_product":
        vector_generator_config = '''"cart_product_vector_generator": {
        "test_factor_values": {\n'''
        tmp = []
        for t in test_factors:
            tmp.append("            \"%s\": []" % t)
        vector_generator_config += ",\n".join(tmp)
        vector_generator_config += '''
        }
    }'''
    elif vector_gen == "simple":
        vector_generator_config = '''"simple_vector_generator": {
        "test_vectors": [
        ]
    }'''
    elif vector_gen == "custom":
        vector_generator_config = '''"custom_vector_generator": {
        "import": "make-case.py",
        "func": "make_vectors",
        "args": {
        }
    }'''
    else:
        raise NotImplementedError()
    if case_gen == "custom":
        case_generator_config = r'''"custom_case_generator": {
        "import": "make-case.py",
        "func": "make_case",
        "args": {
        }
    }'''
    elif case_gen == "template":
        case_generator_config = r'''"template_case_generator": {
        "copy_files": {
        },
        "link_files": {
        },
        "inst_templates": {
            "templates": {
            },
            "variables": {
            }
        },
        "case_spec": {
            "cmd": [CMD, ARGS, ...],
            "envs": {
            },
            "run": {
                "nnodes": NNODES,
                "procs_per_node": PROCS_PER_NODE,
                "tasks_per_proc": TASKS_PER_PROC,
                "nprocs": NPROCS
            },
            "results": [RESULTS],
            "validator": {
                "contains": {}
            }
        }
    }'''
    else:
        raise NotImplementedError()
    tpl = string.Template(TEST_PROJECT_CONFIG_JSON_TPL)
    out = tpl.safe_substitute(
        project_desc=project_desc,
        test_factors=test_factors_repr,
        vector_gen=vector_gen,
        case_gen=case_gen,
        vector_generator_config=vector_generator_config,
        case_generator_config=case_generator_config)
    outfn = os.path.join(project_dir, "TestProjectConfig.json")
    open(outfn, "w").write(out)


CUSTOM_PYTHON_SCRIPT_TPL_P1 = '''#!/usr/bin/env python
#

from __future__ import print_function, unicode_literials
import os
import string
from collections import OrderedDict

'''

CUSTOM_PYTHON_SCRIPT_TPL_P2 = '''
def make_vectors(conf_root, test_factors, **kwargs):
    result = []
    # Create test cases and push them into results
    return result

'''

CUSTOM_PYTHON_SCRIPT_TPL_P3 = '''
def make_case(conf_root, output_root, case_path, test_vector, **kwargs):
    # Expand test vector
    ${test_factors_repr} = test_vector.values()

    # Do input preparation and other heavy stuff
    DO_HEAVY_STUFF()

    # Build case descriptions
    #
    # Important: Please return 'cmd', 'run', 'results' and 'envs'
    bin_path = os.path.join(output_root, "bin", "${binary_name}")
    bin_path = os.path.relpath(bin_path, case_path)
    cmd = [bin_path, ADD_OTHER_ARGS_HERE]
    envs = {
        "OMP_NUM_THREADS": ENVS_HERE,
        "KMP_AFFINITY": "disabled"
    }
    run = {
        "nnodes": NNODES,
        "procs_per_node": PROCS_PER_NODE,
        "tasks_per_proc": TASKS_PER_PROC,
        "nprocs": NPROCS
    }
    results = ["STDOUT"]
    validator = {
        "contains": {}
    }
    return OrderedDict(zip(["cmd", "envs", "run", "results", "validator"],
                           [cmd, envs, run, results, validator]))

'''

CUSTOM_PYTHON_SCRIPT_TPL_P4 = '''
def main():
    pass


if __name__ == "__main__":
    main()
'''


def make_custom_script(template, project_dir, binary_name, test_factors):
    test_factors_repr = ", ".join(test_factors)
    tpl = string.Template(template)
    out = tpl.safe_substitute(
        binary_name=binary_name, test_factors_repr=test_factors_repr)
    outfn = os.path.join(project_dir, "make-case.py")
    open(outfn, "w").write(out)
    os.chmod(outfn, 0o755)


def main():
    parser = argparse.ArgumentParser()
    parser.parse_args()

    show_message("Welcome to the bentoo world. This quickstart will guide "
                 "you through the creation of a bentoo project, by asking "
                 "you simple questions.")
    project_desc = get_input("Project description")
    binary_name = get_input("Execuation binary")
    test_factors = get_list("Test factor names")
    all_vector_gen = ["cart_product", "simple", "custom"]
    vector_gen = get_choice(
        "Test vector generator", [
            "Cartetian product of test factor values (cart_product)",
            "List all cases (simple)", "Custom generator (using python)"
        ],
        default=0)
    all_case_gen = ["custom", "template"]
    case_gen = get_choice(
        "Test case generator", [
            "Custom generator (using python)",
            "Tempalte generator (evaluable templates)"
        ],
        default=0)
    vector_gen = all_vector_gen[vector_gen]
    case_gen = all_case_gen[case_gen]
    project_dir = get_input("Write project to directory")
    make_directories(project_dir)
    make_config(project_dir, project_desc, binary_name, test_factors,
                vector_gen, case_gen)
    if case_gen == "custom" or vector_gen == "custom":
        template = CUSTOM_PYTHON_SCRIPT_TPL_P1
        if vector_gen == "custom":
            template = template + CUSTOM_PYTHON_SCRIPT_TPL_P2
        if case_gen == "custom":
            template = template + CUSTOM_PYTHON_SCRIPT_TPL_P3
        template = template + CUSTOM_PYTHON_SCRIPT_TPL_P4
        make_custom_script(template, project_dir, binary_name, test_factors)


if __name__ == "__main__":
    main()
