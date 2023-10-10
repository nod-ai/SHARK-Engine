# Copyright 2023 Nod Labs, Inc
#
# Licensed under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

import logging
import unittest

import torch
from torch import nn
from torch.utils.data import DataLoader
import torchvision.transforms as transforms
import torchvision.datasets as datasets

torch._dynamo.config.dynamic_shapes = False


class MNISTDataLoader:
    def __init__(self, batch_size, shuffle=True):
        self.batch_size = batch_size
        self.shuffle = shuffle

        transform = transforms.Compose(
            [transforms.ToTensor(), transforms.Normalize((0.5,), (0.5,))]
        )

        self.mnist_trainset = datasets.MNIST(
            root="../data", train=True, download=True, transform=transform
        )
        self.mnist_testset = datasets.MNIST(
            root="../data", train=False, download=True, transform=transform
        )

    def get_train_loader(self):
        return DataLoader(
            dataset=self.mnist_trainset,
            batch_size=self.batch_size,
            shuffle=self.shuffle,
        )

    def get_test_loader(self):
        return DataLoader(
            dataset=self.mnist_testset, batch_size=self.batch_size, shuffle=False
        )


class LinearModel(nn.Module):
    def __init__(self, input_dim, output_dim):
        super(LinearModel, self).__init__()
        self.linear = nn.Linear(input_dim, output_dim)

    def forward(self, x):
        x = x.view(x.size(0), -1)
        out = self.linear(x)
        return out


def test_iteration(model, images):
    outputs = model(images)
    return outputs


def infer():
    # Example Parameters
    config = {
        "batch_size": 100,
        "learning_rate": 0.001,
        "num_epochs": 10,
    }

    custom_data_loader = MNISTDataLoader(config["batch_size"])
    test_loader = custom_data_loader.get_test_loader()

    model = LinearModel(28 * 28, 10)
    test_opt = torch.compile(test_iteration, backend="turbine_cpu")

    for i, (images, labels) in enumerate(test_loader):
        test_opt(model, images)


class ModelTests(unittest.TestCase):
    def testMNIST(self):
        infer()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    unittest.main()
