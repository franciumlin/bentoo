# coding: utf-8

import unittest
import bentoo.common.helpers2 as helpers


class TestHelpers2(unittest.TestCase):
    def test_make_process_grid(self):
        # Each case as: ((nnodes, dim), grid)
        cases = [([6, 2], [3, 2]), ([2, 2], [2, 1]), ([4, 2], [2, 2]),
                 ([8, 2], [4, 2]), ([3072, 2], [64, 48]), ([6, 3], [3, 2, 1]),
                 ([2, 3], [2, 1, 1]), ([16, 3], [4, 2, 2])]
        for args, expect in cases:
            self.assertEqual(helpers.make_process_grid(*args), expect)

    def test_UnstructuredGridModelResizer(self):
        config = [{
            "dim": 3,
            "total_mem": "5G",
            # Each case as: ((mem_per_node, nnodes), (nrefines, nnodes,
            # mem_per_node))
            "cases": [(("4.5G", 1), (0, 1, "5.0G"))]
        }]
        for conf in config:
            resizer = helpers.UnstructuredGridModelResizer(
                conf["dim"], conf["total_mem"])
            for args, expect in conf["cases"]:
                get = resizer.resize(*args)
                self.assertEqual(get["nrefines"], expect[0])
                self.assertEqual(get["nnodes"], expect[1])
                self.assertEqual(get["mem_per_node"], expect[2])


if __name__ == "__main__":
    unittest.main()
