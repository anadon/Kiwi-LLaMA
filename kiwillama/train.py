from datasets import load_dataset
import torch
import os
from transformers import (
    AutoModelForCausalLM,
    BitsAndBytesConfig,
    AutoTokenizer,
    TrainingArguments,
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from trl import SFTTrainer
from kiwillama.utils import find_all_linear_names, print_trainable_parameters
from accelerate import Accelerator

from kiwillama import settings

accelerator = Accelerator()
device_index = Accelerator().process_index
device_map = {"": device_index}

token = settings.HUGGING_FACE_API_TOKEN  # your token
# output_dir = "./models/Llama-3-8b-mix-2epoch"
output_dir = settings.TRAINING_OUTPUT_DIR
model_name = "meta-llama/Meta-Llama-3-8B-Instruct"

os.environ["WANDB_PROJECT"] = output_dir.split("/")[-1]


train_dataset = load_dataset(
    "csv",
    data_files=[
        "./data/train/MTSample/NER_main_train_MTSample.csv",
        "./data/train/MTSample/RE_LLAMA2_train_MTSample.csv",
    ],
    split="train",
)


bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.bfloat16,
)

base_model = AutoModelForCausalLM.from_pretrained(
    model_name,
    torch_dtype=torch.bfloat16,
    quantization_config=bnb_config,
    device_map=device_map,
    cache_dir="/data/yhu5/huggingface_models/",
    token=token,
)


base_model.config.use_cache = False
base_model = prepare_model_for_kbit_training(base_model)

tokenizer = AutoTokenizer.from_pretrained(model_name, token=token, use_fast=True)
tokenizer.pad_token = tokenizer.eos_token
tokenizer.padding_side = "right"

peft_config = LoraConfig(
    r=16,
    lora_alpha=64,
    target_modules=find_all_linear_names(base_model),
    # target_modules=["q_proj", "v_proj"],
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM",
)

base_model = get_peft_model(base_model, peft_config)
print_trainable_parameters(base_model)
# base_model = accelerator.prepare(base_model)


def formatting_prompts_func(example):
    return [
        f"{lr[0]} {lr[1]}" for lr in zip(example["unprocessed"], example["processed"])
    ]


# Parameters for training arguments details => https://github.com/huggingface/transformers/blob/main/src/transformers/training_args.py#L158
training_args = TrainingArguments(
    per_device_train_batch_size=4,
    gradient_accumulation_steps=4,
    gradient_checkpointing=True,
    max_grad_norm=0.3,
    num_train_epochs=2,
    learning_rate=2e-4,
    bf16=True,
    save_strategy="epoch",
    save_total_limit=10,
    logging_steps=10,
    output_dir=output_dir,
    optim="paged_adamw_32bit",
    lr_scheduler_type="cosine",
    warmup_ratio=0.05,
    ddp_find_unused_parameters=False,
    evaluation_strategy="epoch",
    # load_best_model_at_end=True,
    # metric_for_best_model='eval_loss'
)

trainer = SFTTrainer(
    base_model,
    train_dataset=train_dataset,
    # eval_dataset=eval_dataset,
    tokenizer=tokenizer,
    max_seq_length=1000,
    formatting_func=formatting_prompts_func,
    args=training_args,
    # callbacks = [EarlyStoppingCallback(early_stopping_patience=3)]
)

# trainer.train(resume_from_checkpoint=True)
trainer.train()
trainer.save_model(output_dir)

output_dir = os.path.join(output_dir, "final_checkpoint")
trainer.model.save_pretrained(output_dir)
tokenizer.save_pretrained(output_dir)
