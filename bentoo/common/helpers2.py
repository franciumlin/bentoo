# coding: utf-8
#

import functools
import math
import re


def sizeToFloat(s):
    if isinstance(s, (int, float)):
        return float(s)
    unitValue = {"b": 1, "k": 1024, "m": 1024 * 1024, "g": 1024 * 1024 * 1024}
    regex = re.compile(
        r"(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)([kmgKMG](?:i?[Bb])?)?")
    m = regex.match(s)
    if m is None:
        raise RuntimeError("Invalid size representation '%s'" % s)
    value = float(m.group(1))
    unit = m.group(2)
    unit = unit[0].lower() if unit else 'b'
    return value * unitValue[unit]


def floatToSize(f):
    if not isinstance(f, (int, float)):
        return f
    if f < 1024:
        return "{:.1f}".format(f)
    elif f < 1024 * 1024:
        return "{:.1f}K".format(f / 1024.0**1)
    elif f < 1024 * 1024 * 1024:
        return "{:.1f}M".format(f / 1024.0**2)
    else:
        return "{:.1f}G".format(f / 1024.0**3)


class StructuredGridModelResizer(object):
    '''Resize a structured grid model

    This resizer assumes the model uses a structured grid and the grid can be
    regrided arbitrarily to fit any total memory requirements.
    '''

    def __init__(self, grid, total_mem):
        '''Initialize the resizer

        :grid: The shape of the grid, integer list
        :total_mem: The total memory of the model, float or string. Strings will
        be recogonized as '13.3K/KB/Kib/k/kb'"
        '''
        self.grid = grid
        self.dim = len(grid)
        self.total_mem = sizeToFloat(total_mem)

    def resize(self, mem_per_node):
        '''Resize the model to reach a certain memory per node

        :mem_per_node: The memory per node required, float or string.

        :return: The new grid and nnodes for the model.
        '''
        mem_per_node = sizeToFloat(mem_per_node)
        ncells = functools.reduce(lambda x, y: x * y, self.grid)
        bytes_per_cell = self.total_mem / ncells
        new_ncells = mem_per_node / bytes_per_cell
        nx = int(math.floor(new_ncells**(1.0 / self.dim)))
        base_grid = [nx for i in range(self.dim)]
        new_ncells = functools.reduce(lambda x, y: x * y, base_grid)
        new_mem_per_node = bytes_per_cell * new_ncells
        return {
            "nnodes": 1,
            "grid": base_grid,
            "mem_per_node": floatToSize(new_mem_per_node),
            "index_": 0
        }

    def next(self, model):
        result = dict(model)
        result["nnodes"] *= 2
        result["grid"][result["index_"]] *= 2
        result["index_"] = (result["index_"] + 1) % self.dim
        return result


class UnstructuredGridModelResizer(object):
    '''Resize an unstructured grid model

    This resizer assumes the grid is unstructured and one can only refine
    uniformlly the grid to reach a given size.
    '''

    def __init__(self, dim, total_mem):
        '''Initialize the resizer

        :dim: The dim of the model, integer
        :total_mem: The total memory of the model, float or string. Strings will
        be recogonized as '13.3K/KB/Kib/k/kb'"
        '''
        self.dim = int(dim)
        self.total_mem = sizeToFloat(total_mem)
        self.stride = 2**self.dim

    def resize(self, mem_per_node):
        '''Resize the model to reach a certain memory per node

        :mem_per_node: The memory per node required, float or string.

        :return: The new grid and nnodes for the model.
        '''
        mem_per_node = sizeToFloat(mem_per_node)
        rel = math.fabs((mem_per_node - self.total_mem) / self.total_mem)
        # If the request is within 10% of the original model, use it.
        if (rel < 0.1):
            return {
                "nrefines": 1,
                "nnodes": 1,
                "mem_per_node": floatToSize(self.total_mem)
            }
        # If the request is bigger, enlarge it (may change the nnodes).
        # Otherwise, change the nnodes to reduce the memory per node.
        if mem_per_node > self.total_mem:
            ratio = mem_per_node / self.total_mem
            i = int(math.floor(math.log(ratio, self.stride)))
            real_mem = self.total_mem * self.stride**i
            if real_mem * 2 < mem_per_node:
                # The resized model is too small from the required size, we have
                # to enlarge more and change the nnodes as well.
                i += 1
                real_mem = self.total_mem * self.stride**i
                assert (real_mem > mem_per_node)
                nnodes = int(math.ceil(real_mem / mem_per_node))
                mem_per_node = real_mem / nnodes
                return {
                    "nrefines": i,
                    "nnodes": nnodes,
                    "mem_per_node": floatToSize(mem_per_node)
                }
            else:
                return {
                    "nrefines": i,
                    "nnodes": 1,
                    "mem_per_node": floatToSize(real_mem)
                }
        else:
            nnodes = int(math.ceil(self.total_mem / mem_per_node))
            mem_per_node = self.total_mem / nnodes
            return {
                "nrefines": 1,
                "nnodes": nnodes,
                "mem_per_node": floatToSize(mem_per_node)
            }

    def next(self, model):
        result = dict(model)
        result["nnodes"] *= self.stride
        result["nrefines"] += 1
        return result


def make_process_grid(n, dim):
    pass
