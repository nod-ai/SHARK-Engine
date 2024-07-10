# Copyright 2024 Advanced Micro Devices, Inc.
#
# Licensed under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

import logging
import pytest
import torch
from transformers import CLIPTokenizer
from turbine_models.custom_models.sd_inference.utils import create_safe_name
from turbine_models.custom_models.sd3_inference.text_encoder_impls import SD3Tokenizer
from turbine_models.custom_models.sd3_inference import (
    sd3_text_encoders,
    sd3_text_encoders_runner,
    sd3_mmdit,
    sd3_mmdit_runner,
    sd3_vae,
    sd3_vae_runner,
    sd3_pipeline,
    sd3_schedulers,
)
from turbine_models.custom_models.sd_inference import utils
from turbine_models.custom_models.sd3_inference.sd3_text_encoders import (
    TextEncoderModule,
)
from turbine_models.utils.sdxl_benchmark import run_benchmark
import unittest
from tqdm.auto import tqdm
from PIL import Image
import os
import numpy as np
import time


torch.random.manual_seed(0)

arguments = {}


@pytest.fixture(scope="session")
def command_line_args(request):
    arguments["hf_auth_token"] = request.config.getoption("--hf_auth_token")
    arguments["hf_model_name"] = request.config.getoption("--hf_model_name")
    arguments["scheduler_id"] = request.config.getoption("--scheduler_id")
    arguments["model_path"] = request.config.getoption("--model_path")
    arguments["vae_model_path"] = request.config.getoption("--vae_model_path")
    arguments["prompt"] = request.config.getoption("--prompt")
    arguments["negative_prompt"] = request.config.getoption("--negative_prompt")
    arguments["num_inference_steps"] = int(
        request.config.getoption("--num_inference_steps")
    )
    arguments["guidance_scale"] = float(request.config.getoption("--guidance_scale"))
    arguments["seed"] = float(request.config.getoption("--seed"))
    arguments["denoise"] = request.config.getoption("--denoise")
    arguments["external_weight_path"] = request.config.getoption(
        "--external_weight_path"
    )
    arguments["external_weight_dir"] = request.config.getoption("--external_weight_dir")
    arguments["external_weight_file"] = request.config.getoption(
        "--external_weight_file"
    )
    arguments["vmfb_path"] = request.config.getoption("--vmfb_path")
    arguments["pipeline_vmfb_path"] = request.config.getoption("--pipeline_vmfb_path")
    arguments["scheduler_vmfb_path"] = request.config.getoption("--scheduler_vmfb_path")
    arguments["split_scheduler"] = request.config.getoption("--split_scheduler")
    arguments["cpu_scheduling"] = request.config.getoption("--cpu_scheduling")
    arguments["pipeline_dir"] = request.config.getoption("--pipeline_dir")
    arguments["compiled_pipeline"] = request.config.getoption("--compiled_pipeline")
    arguments["npu_delegate_path"] = request.config.getoption("--npu_delegate_path")
    arguments["clip_device"] = request.config.getoption("--clip_device")
    arguments["mmdit_device"] = request.config.getoption("--mmdit_device")
    arguments["vae_device"] = request.config.getoption("--vae_device")
    arguments["clip_target"] = request.config.getoption("--clip_target")
    arguments["vae_target"] = request.config.getoption("--vae_target")
    arguments["mmdit_target"] = request.config.getoption("--mmdit_target")
    arguments["batch_size"] = int(request.config.getoption("--batch_size"))
    arguments["height"] = int(request.config.getoption("--height"))
    arguments["width"] = int(request.config.getoption("--width"))
    arguments["precision"] = request.config.getoption("--precision")
    arguments["vae_precision"] = request.config.getoption("--vae_precision")
    arguments["max_length"] = int(request.config.getoption("--max_length"))
    arguments["vae_variant"] = request.config.getoption("--vae_variant")
    arguments["shift"] = request.config.getoption("--shift")
    arguments["vae_decomp_attn"] = request.config.getoption("--vae_decomp_attn")
    arguments["vae_dtype"] = request.config.getoption("--vae_dtype")
    arguments["external_weights"] = request.config.getoption("--external_weights")
    arguments["decomp_attn"] = request.config.getoption("--decomp_attn")
    arguments["exit_on_vmfb"] = request.config.getoption("--exit_on_vmfb")
    arguments["output"] = request.config.getoption("--output")
    arguments["attn_spec"] = request.config.getoption("--attn_spec")
    arguments["device"] = request.config.getoption("--device")
    arguments["rt_device"] = request.config.getoption("--rt_device")
    arguments["iree_target_triple"] = request.config.getoption("--iree_target_triple")
    arguments["ireec_flags"] = request.config.getoption("--ireec_flags")
    arguments["attn_flags"] = request.config.getoption("--attn_flags")
    arguments["clip_flags"] = request.config.getoption("--clip_flags")
    arguments["vae_flags"] = request.config.getoption("--vae_flags")
    arguments["mmdit_flags"] = request.config.getoption("--mmdit_flags")


@pytest.mark.usefixtures("command_line_args")
class StableDiffusion3Test(unittest.TestCase):
    def setUp(self):
        self.safe_model_name = create_safe_name(arguments["hf_model_name"], "")
        self.mmdit_model = sd3_mmdit.MMDiTModel(
            arguments["hf_model_name"],
            precision=arguments["precision"],
        )
        self.vae_model = sd3_vae.VaeModel(
            # This is a public model, so no auth required
            arguments["hf_model_name"],
            custom_vae=(
                "madebyollin/sdxl-vae-fp16-fix"
                if arguments["precision"] == "fp16"
                else None
            ),
        )

    def test01_ExportPromptEncoder(self):
        if arguments["device"] in ["vulkan", "cuda"]:
            self.skipTest("Not testing sd3 on vk or cuda")
        arguments["external_weight_path"] = (
            arguments["external_weight_path"]
            + "/sd3_text_encoders_"
            + arguments["precision"]
            + ".irpa"
        )
        _, prompt_encoder_vmfb = sd3_text_encoders.export_text_encoders(
            arguments["hf_model_name"],
            hf_auth_token=None,
            max_length=arguments["max_length"],
            precision=arguments["precision"],
            compile_to="vmfb",
            external_weights=arguments["external_weights"],
            external_weight_path=arguments["external_weight_path"],
            device=arguments["device"],
            target_triple=arguments["clip_target"],
            ireec_flags=arguments["ireec_flags"],
            exit_on_vmfb=True,
            pipeline_dir=arguments["pipeline_dir"],
            input_mlir=None,
            attn_spec=arguments["attn_spec"],
            output_batchsize=arguments["batch_size"],
            decomp_attn=arguments["decomp_attn"],
        )
        tokenizer = SD3Tokenizer()
        (
            text_input_ids_list,
            uncond_input_ids_list,
        ) = sd3_text_encoders_runner.run_tokenize(
            tokenizer,
            arguments["prompt"],
            arguments["negative_prompt"],
        )
        (
            turbine_output1,
            turbine_output2,
        ) = sd3_text_encoders_runner.run_prompt_encoder(
            prompt_encoder_vmfb,
            arguments["rt_device"],
            arguments["external_weight_path"],
            text_input_ids_list,
            uncond_input_ids_list,
        )
        torch_encoder_model = TextEncoderModule(
            arguments["batch_size"],
        )
        torch_output1, torch_output2 = torch_encoder_model.forward(
            *text_input_ids_list, *uncond_input_ids_list
        )
        rtol = 4e-2
        atol = 4e-2
        np.testing.assert_allclose(torch_output1, turbine_output1, rtol, atol)
        np.testing.assert_allclose(torch_output2, turbine_output2, rtol, atol)

    def test02_ExportMMDITModel(self):
        if arguments["device"] in ["vulkan", "cuda"]:
            self.skipTest("Not testing on vulkan or cuda")
        arguments["external_weight_path"] = (
            self.safe_model_name
            + "_"
            + arguments["precision"]
            + "_mmdit."
            + arguments["external_weights"]
        )
        sd3_mmdit.export_mmdit_model(
            mmdit_model=self.mmdit_model,
            # This is a public model, so no auth required
            hf_model_name=arguments["hf_model_name"],
            batch_size=arguments["batch_size"],
            height=arguments["height"],
            width=arguments["width"],
            precision=arguments["precision"],
            max_length=arguments["max_length"],
            hf_auth_token=None,
            compile_to="vmfb",
            external_weights=arguments["external_weights"],
            external_weight_path=arguments["external_weight_path"],
            device=arguments["mmdit_device"],
            target_triple=arguments["iree_target_triple"],
            ireec_flags=arguments["ireec_flags"],
            decomp_attn=arguments["decomp_attn"],
            attn_spec=arguments["attn_spec"],
        )
        arguments["vmfb_path"] = (
            self.safe_model_name
            + "_"
            + str(arguments["max_length"])
            + "_"
            + str(arguments["height"])
            + "x"
            + str(arguments["width"])
            + "_"
            + arguments["precision"]
            + "_unet_"
            + arguments["device"]
            + ".vmfb"
        )
        dtype = torch.float16 if arguments["precision"] == "fp16" else torch.float32

        hidden_states = torch.randn(
            (
                arguments["batch_size"],
                16,
                arguments["height"] // 8,
                arguments["width"] // 8,
            ),
            dtype=dtype,
        )
        encoder_hidden_states = torch.randn(
            (arguments["batch_size"], arguments["max_length"] * 2, 4096), dtype=dtype
        )
        pooled_projections = torch.randn((arguments["batch_size"], 2048), dtype=dtype)
        timestep = torch.tensor([0, 0], dtype=dtype)
        turbine = sd3_mmdit_runner.run_mmdit_turbine(
            hidden_states,
            encoder_hidden_states,
            pooled_projections,
            timestep,
            arguments,
        )
        torch_output = sd3_mmdit_runner.run_diffusers_mmdit(
            hidden_states,
            encoder_hidden_states,
            pooled_projections,
            timestep,
            arguments,
        )
        #        if arguments["benchmark"] or arguments["tracy_profile"]:
        #            run_benchmark(
        #                "unet",
        #                arguments["vmfb_path"],
        #                arguments["external_weight_path"],
        #                arguments["rt_device"],
        #                max_length=arguments["max_length"],
        #                height=arguments["height"],
        #                width=arguments["width"],
        #                batch_size=arguments["batch_size"],
        #                in_channels=arguments["in_channels"],
        #                precision=arguments["precision"],
        #                tracy_profile=arguments["tracy_profile"],
        #            )
        rtol = 4e-2
        atol = 4e-1

        np.testing.assert_allclose(torch_output, turbine, rtol, atol)

    def test03_ExportVaeModelDecode(self):
        if arguments["device"] in ["vulkan", "cuda"]:
            self.skipTest("not testing vulkan or cuda")
        sd3_vae.export_vae_model(
            vae_model=self.vae_model,
            # This is a public model, so no auth required
            exit_on_vmfb=True,
        )

        arguments["external_weight_path"] = (
            self.safe_model_name
            + "_"
            + arguments["precision"]
            + "_vae_decode."
            + arguments["external_weights"]
        )
        sd3_vae.export_vae_model(
            self.vae_model,
            hf_model_name=arguments["hf_model_name"],
            batch_size=arguments["batch_size"],
            height=arguments["height"],
            width=arguments["width"],
            precision=arguments["precision"],
            compile_to="vmfb",
            external_weights=arguments["external_weights"],
            external_weight_path=arguments["external_weight_path"],
            device=arguments["device"],
            target_triple=arguments["iree_target_triple"],
            ireec_flags=arguments["ireec_flags"],
            variant="decode",
            decomp_attn=arguments["decomp_attn"],
            attn_spec=arguments["attn_spec"],
        )
        arguments["vmfb_path"] = (
            self.safe_model_name
            + "_"
            + str(arguments["height"])
            + "x"
            + str(arguments["width"])
            + "_"
            + arguments["precision"]
            + "_vae_decode_"
            + arguments["device"]
            + ".vmfb"
        )
        example_input = torch.ones(
            arguments["batch_size"],
            16,
            arguments["height"] // 8,
            arguments["width"] // 8,
            dtype=torch.float32,
        )
        example_input_torch = example_input
        if arguments["precision"] == "fp16":
            example_input = example_input.half()
        turbine = sd3_vae_runner.run_vae(
            arguments["rt_device"],
            example_input,
            arguments["vmfb_path"],
            arguments["hf_model_name"],
            arguments["external_weight_path"],
        )
        torch_output = sd3_vae_runner.run_torch_vae(
            arguments["hf_model_name"],
            (
                "madebyollin/sdxl-vae-fp16-fix"
                if arguments["precision"] == "fp16"
                else ""
            ),
            "decode",
            example_input_torch,
        )
        # if arguments["benchmark"] or arguments["tracy_profile"]:
        #    run_benchmark(
        #        "vae_decode",
        #        arguments["vmfb_path"],
        #        arguments["external_weight_path"],
        #        arguments["rt_device"],
        #        height=arguments["height"],
        #        width=arguments["width"],
        #        precision=arguments["precision"],
        #        tracy_profile=arguments["tracy_profile"],
        #    )
        rtol = 4e-2
        atol = 4e-1

        np.testing.assert_allclose(torch_output, turbine, rtol, atol)

    @pytest.mark.skip("Waiting on inference plumbing for generalized sd pipeline")
    def test04SDPipeline(self):
        from turbine_models.custom_models.sd_inference.sd_pipeline import (
            SharkSDPipeline,
        )

        current_args = copy.deepcopy(default_arguments)
        decomp_attn = {
            "text_encoder": False,
            "unet": False,
            "vae": current_args["vae_decomp_attn"],
        }
        sd_pipe = SharkSDPipeline(
            current_args["hf_model_name"],
            current_args["height"],
            current_args["width"],
            current_args["batch_size"],
            current_args["max_length"],
            current_args["precision"],
            current_args["device"],
            current_args["iree_target_triple"],
            ireec_flags=None,  # ireec_flags
            attn_spec=current_args["attn_spec"],
            decomp_attn=decomp_attn,
            pipeline_dir="test_vmfbs",  # pipeline_dir
            external_weights_dir="test_weights",  # external_weights_dir
            external_weights=current_args["external_weights"],
            num_inference_steps=current_args["num_inference_steps"],
            cpu_scheduling=True,
            scheduler_id=current_args["scheduler_id"],
            shift=None,  # shift
            use_i8_punet=False,
        )
        sd_pipe.prepare_all()
        sd_pipe.load_map()
        output = sd_pipe.generate_images(
            current_args["prompt"],
            current_args["negative_prompt"],
            current_args["num_inference_steps"],
            1,  # batch count
            current_args["guidance_scale"],
            current_args["seed"],
            current_args["cpu_scheduling"],
            current_args["scheduler_id"],
            True,  # return_img
        )
        assert output is not None


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    unittest.main()
