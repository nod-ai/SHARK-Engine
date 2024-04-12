import argparse
from turbine_models.model_runner import vmfbRunner
from transformers import CLIPTokenizer
from iree import runtime as ireert
import torch
import numpy as np


def run_encode_prompts(
    device,
    prompt,
    negative_prompt,
    vmfb_path_1,
    vmfb_path_2,
    hf_model_name,
    hf_auth_token,
    external_weight_path_1,
    external_weight_path_2,
    max_length,
):
    runner_1 = vmfbRunner(device, vmfb_path_1, external_weight_path_1)
    runner_2 = vmfbRunner(device, vmfb_path_2, external_weight_path_2)
    text_encoders = [runner_1, runner_2]

    tokenizer_1 = CLIPTokenizer.from_pretrained(
        hf_model_name,
        subfolder="tokenizer",
        token=hf_auth_token,
    )
    tokenizer_2 = CLIPTokenizer.from_pretrained(
        hf_model_name,
        subfolder="tokenizer_2",
        token=hf_auth_token,
    )
    tokenizers = [tokenizer_1, tokenizer_2]
    prompt_embeds_list = []
    prompts = [prompt, prompt]
    for prompt, tokenizer, text_encoder in zip(prompts, tokenizers, text_encoders):
        text_inputs = tokenizer(
            prompt,
            padding="max_length",
            max_length=max_length,
            truncation=True,
            return_tensors="pt",
        )

        text_input_ids = text_inputs.input_ids
        untruncated_ids = tokenizer(
            prompt, padding="longest", return_tensors="pt"
        ).input_ids

        if untruncated_ids.shape[-1] >= text_input_ids.shape[-1] and not torch.equal(
            text_input_ids, untruncated_ids
        ):
            removed_text = tokenizer.batch_decode(
                untruncated_ids[:, max_length - 1 : -1]
            )
            print(
                "The following part of your input was truncated because CLIP can only handle sequences up to"
                f" {max_length} tokens: {removed_text}"
            )
        text_input_ids = [
            ireert.asdevicearray(text_encoder.config.device, text_input_ids)
        ]
        text_encoder_output = text_encoder.ctx.modules.compiled_clip["main"](
            *text_input_ids
        )
        prompt_embeds = torch.from_numpy(text_encoder_output[0].to_host())
        pooled_prompt_embeds = torch.from_numpy(text_encoder_output[1].to_host())

        prompt_embeds_list.append(prompt_embeds)

    prompt_embeds = torch.concat(prompt_embeds_list, dim=-1)

    uncond_tokens = [negative_prompt, negative_prompt]
    negative_prompt_embeds_list = []
    for negative_prompt, tokenizer, text_encoder in zip(
        uncond_tokens, tokenizers, text_encoders
    ):
        uncond_input = tokenizer(
            negative_prompt,
            padding="max_length",
            max_length=max_length,
            truncation=True,
            return_tensors="pt",
        )

        uncond_input_ids = uncond_input.input_ids
        uncond_input_ids = [
            ireert.asdevicearray(text_encoder.config.device, uncond_input_ids)
        ]

        text_encoder_output = text_encoder.ctx.modules.compiled_clip["main"](
            *uncond_input_ids
        )
        negative_prompt_embeds = torch.from_numpy(text_encoder_output[0].to_host())
        negative_pooled_prompt_embeds = torch.from_numpy(
            text_encoder_output[1].to_host()
        )

        negative_prompt_embeds_list.append(negative_prompt_embeds)

    negative_prompt_embeds = torch.concat(negative_prompt_embeds_list, dim=-1)

    do_classifier_free_guidance = True

    bs_embed, seq_len, _ = prompt_embeds.shape
    prompt_embeds = prompt_embeds.repeat(1, 1, 1)
    prompt_embeds = prompt_embeds.view(bs_embed * 1, seq_len, -1)
    if do_classifier_free_guidance:
        negative_pooled_prompt_embeds = negative_pooled_prompt_embeds.repeat(1, 1).view(
            1, -1
        )
        negative_prompt_embeds = negative_prompt_embeds.repeat(1, 1, 1)
        negative_prompt_embeds = negative_prompt_embeds.view(bs_embed * 1, seq_len, -1)

    pooled_prompt_embeds = pooled_prompt_embeds.repeat(1, 1).view(bs_embed * 1, -1)
    return (
        prompt_embeds,
        negative_prompt_embeds,
        pooled_prompt_embeds,
        negative_pooled_prompt_embeds,
    )


def run_torch_clip(hf_model_name, hf_auth_token, prompt, max_length=64):
    # TODO: Integrate with HFTransformerBuilder
    from turbine_models.custom_models.sdxl_inference.clip import ClipModel

    model_1 = ClipModel(hf_model_name, hf_auth_token, index=1)
    model_2 = ClipModel(hf_model_name, hf_auth_token, index=2)
    tokenizer_1 = CLIPTokenizer.from_pretrained(
        hf_model_name,
        subfolder="tokenizer",
        token=hf_auth_token,
    )
    tokenizer_2 = CLIPTokenizer.from_pretrained(
        hf_model_name,
        subfolder="tokenizer_2",
        token=hf_auth_token,
    )
    text_input_1 = tokenizer_1(
        prompt,
        padding="max_length",
        max_length=max_length,
        truncation=True,
        return_tensors="pt",
    )
    text_input_2 = tokenizer_2(
        prompt,
        padding="max_length",
        max_length=max_length,
        truncation=True,
        return_tensors="pt",
    )
    example_input_1 = text_input_1.input_ids
    example_input_2 = text_input_2.input_ids

    results_1 = model_1.forward(example_input_1)
    results_2 = model_2.forward(example_input_2)
    np_torch_output_1 = results_1[0].detach().cpu().numpy().astype(np.float16)
    np_torch_output_2 = results_2[0].detach().cpu().numpy().astype(np.float16)
    return np_torch_output_1, np_torch_output_2


def run_clip(
    device,
    prompt,
    vmfb_path,
    hf_model_name,
    hf_auth_token,
    external_weight_path,
    max_length,
    index,
):
    runner = vmfbRunner(device, vmfb_path, external_weight_path)

    if index == 1:
        tokenizer = CLIPTokenizer.from_pretrained(
            hf_model_name,
            subfolder="tokenizer",
            token=hf_auth_token,
        )
    elif index == 2:
        tokenizer = CLIPTokenizer.from_pretrained(
            hf_model_name,
            subfolder="tokenizer_2",
            token=hf_auth_token,
        )
    else:
        print("Incorrect CLIP model index, please use 1 or 2")
        exit(1)

    text_input = tokenizer(
        prompt,
        padding="max_length",
        max_length=max_length,
        truncation=True,
        return_tensors="pt",
    )
    example_input = text_input.input_ids
    inp = [ireert.asdevicearray(runner.config.device, example_input)]
    results = runner.ctx.modules.compiled_clip["main"](*inp)

    return results


if __name__ == "__main__":
    from turbine_models.custom_models.sdxl_inference.sdxl_cmd_opts import args

    vmfb_path_1 = "_clip_1".join(args.vmfb_path.split("_clip"))
    vmfb_path_2 = "_clip_2".join(args.vmfb_path.split("_clip"))
    external_weight_path_1 = "_clip_1".join(args.external_weight_path.split("_clip"))
    external_weight_path_2 = "_clip_2".join(args.external_weight_path.split("_clip"))
    turbine_output1 = run_clip(
        args.device,
        args.prompt,
        vmfb_path_1,
        args.hf_model_name,
        args.hf_auth_token,
        external_weight_path_1,
        args.max_length,
        index=1,
    )
    print(
        "TURBINE OUTPUT 1:",
        turbine_output1[0].to_host(),
        turbine_output1[0].to_host().shape,
        turbine_output1[0].to_host().dtype,
    )

    turbine_output2 = run_clip(
        args.device,
        args.prompt,
        vmfb_path_2,
        args.hf_model_name,
        args.hf_auth_token,
        external_weight_path_2,
        args.max_length,
        index=2,
    )
    print(
        "TURBINE OUTPUT 2:",
        turbine_output2[0].to_host(),
        turbine_output2[0].to_host().shape,
        turbine_output2[0].to_host().dtype,
    )
    if args.compare_vs_torch:
        print("generating torch output: ")
        from turbine_models.custom_models.sd_inference import utils

        torch_output1, torch_output2 = run_torch_clip(
            args.hf_model_name,
            args.hf_auth_token,
            args.prompt,
            args.max_length,
        )
        print(
            "TORCH OUTPUT 1:", torch_output1, torch_output1.shape, torch_output1.dtype
        )

        print(
            "TORCH OUTPUT 2:", torch_output2, torch_output2.shape, torch_output2.dtype
        )
        rtol = 4e-1
        atol = 4e-2
        np.testing.assert_allclose(
            torch_output1, turbine_output1[0], rtol, atol, verbose=True
        )
        np.testing.assert_allclose(
            torch_output2, turbine_output2[0], rtol, atol, verbose=True
        )
    # TODO: Figure out why we occasionally segfault without unlinking output variables
    turbine_output1, turbine_output2 = (None, None)
