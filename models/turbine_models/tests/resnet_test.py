import argparse
import logging
from turbine_models.custom_models import resnet_18
import unittest
import os
import pytest

arguments = {
    "run_vmfb": True,
    "compile_to": None,
    "vmfb_path": "",
    "device": "local-task",
    "iree_target_triple": "",
    "vulkan_max_allocation": "4294967296",
}

resnet_model = resnet_18.Resnet18Model()


class Resnet18Test(unittest.TestCase):
    def testExportResnet18Model(self):
        resnet_18.export_resnet_18_model(
            resnet_model,
            "vmfb",
            "cpu",
        )
        namespace = argparse.Namespace(**arguments)
        resnet_18.run_resnet_18_vmfb_comparison(resnet_model, namespace)
        os.remove("resnet_18.vmfb")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    unittest.main()
