# Design of BenchTools

## Rationale

BenchTools is a set of tool to make parallel benchmarking easy and consistant.
It defines a specification for benchmark definition, result layout as well as
for result file.

## Concepts

```python
def function():
    pass
```

## Interfaces

## Components

BenchTools consist of a runner, a collector and a analyser. The runner runs test
defined in a hierarchy and store raw results in a well-defined directory
structure. The collector can then be used to gather results in the result
directory using information from the case definition, the gathered information
is stored in a sqlite3 database will a well-defined table structure. The
analyser is a simple tool for simple data analysis, which read the database
generated by Collector. More complicated analysis can be carried out using
pandas and other tools. wite some english to be very disidfign. 写一点中文。
