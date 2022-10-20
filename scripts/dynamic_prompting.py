import os
from pathlib import Path
import logging
import math
import re, random
import pathlib
from typing import Set

import gradio as gr
import modules.scripts as scripts

from modules.processing import process_images, fix_seed
from modules.shared import opts

logger = logging.getLogger(__name__)

WILDCARD_DIR = getattr(opts, "wildcard_dir", "scripts/wildcards")
MAX_RECURSIONS = 20
VERSION = "0.4.2"

re_wildcard = re.compile(r"__(.*?)__")
re_combinations = re.compile(r"\{([^{}]*)}")

DEFAULT_NUM_COMBINATIONS = 1

def replace_combinations(match):
    if match is None or len(match.groups()) == 0:
        logger.warning("Unexpected missing combination")
        return ""

    variants = [s.strip() for s in match.groups()[0].split("|")]
    if len(variants) > 0:
        first = variants[0].split("$$")
        quantity = DEFAULT_NUM_COMBINATIONS
        if len(first) == 2: # there is a $$
            prefix_num, first_variant = first
            variants[0] = first_variant
            
            try:
                prefix_ints = [int(i) for i in prefix_num.split("-")]
                if len(prefix_ints) == 1:
                    quantity = prefix_ints[0]
                elif len(prefix_ints) == 2:
                    prefix_low = min(prefix_ints)
                    prefix_high = max(prefix_ints)
                    quantity = random.randint(prefix_low, prefix_high)
                else:
                    raise 
            except Exception:
                logger.warning(f"Unexpected combination formatting, expected $$ prefix to be a number or interval. Defaulting to {DEFAULT_NUM_COMBINATIONS}")
        
        try:
            picked = random.sample(variants, quantity)
            return ", ".join(picked)
        except ValueError as e:
            logger.exception(e)
            return ""

    return ""


def replace_wildcard(match):
    if match is None or len(match.groups()) == 0:
        logger.warning("Expected match to contain a filename")
        return ""

    wildcard_dir = Path(WILDCARD_DIR)
    if not wildcard_dir.exists():
        wildcard_dir.mkdir()

    wildcard = match.groups()[0]
    txt_files = list(pathlib.Path(wildcard_dir).rglob("*.txt"))

    replacement_files = []
    for path in txt_files:
        if wildcard in str(path.absolute()) or os.path.normpath(wildcard) in str(path.absolute()):
            replacement_files.append(str(path.absolute()))
        else:
            if "*" in wildcard:
                terms = [m for m in wildcard.__str__().split("*")]
                matched_terms = []
                for term in terms:
                    if term in str(path.absolute()) or os.path.normpath(term) in str(path.absolute()):
                        matched_terms.append(term)
                if len(terms) == len(matched_terms):
                    replacement_files.append(str(path.absolute()))

    contents: Set = set()
    for replacement_file in replacement_files:
        if os.path.exists(replacement_file):
            with open(replacement_file, encoding="utf8", errors="ignore") as f:
                lines = f.read().splitlines()
                filtered = [line.strip() for line in lines if not line.startswith(
                    '#') and line is not None and len(line) > 0]
                contents.update(filtered)
    options = list(contents)

    return random.choice(options)
    
def pick_wildcards(template):
    return re_wildcard.sub(replace_wildcard, template)


def pick_variant(template):
    if template is None:
        return None

    return re_combinations.sub(replace_combinations, template)

def generate_prompt(template, replace_underscore):
    old_prompt = template
    counter = 0
    while True:
        counter += 1
        if counter > MAX_RECURSIONS:
            raise Exception("Too many recursions, something went wrong with generating the prompt")

        prompt = pick_variant(old_prompt)
        prompt = pick_wildcards(prompt)

        if replace_underscore:
            prompt = prompt.replace("_"," ")
        if prompt == old_prompt:
            return prompt
        old_prompt = prompt
        
class Script(scripts.Script):
    def title(self):
        return f"Dynamic Prompting v{VERSION}"

    def ui(self, is_img2img):
        replace_underscore = gr.Checkbox(label='Replace underscores from danbooru tags', value=False)
        html = f"""
            <h3><strong>Combinations</strong></h3>
            Choose a number of terms from a list, in this case we choose two artists
            <code>{{2$$artist1|artist2|artist3}}</code>
            If $$ is not provided, then 1$$ is assumed.
            <br>
            A range can be provided:
            <code>{{1-3$$artist1|artist2|artist3}}</code>
            In this case, a random number of artists between 1 and 3 is chosen.
            <br/><br/>

            <h3><strong>Wildcards</strong></h3>
            <p>Available wildcards</p>
            <ul>
        """
        
        for path in Path(WILDCARD_DIR).glob("*.txt"):
            filename = path.name
            wildcard = "__" + filename.replace(".txt", "") + "__"

            html += f"<li>{wildcard}</li>"

        html += "</ul>"
        html += f"""
            <br/>
            <code>WILDCARD_DIR: {WILDCARD_DIR}</code><br/>
            <small>You can add more wildcards by creating a text file with one term per line and name is mywildcards.txt. Place it in {WILDCARD_DIR}. <code>__mywildcards__</code> will then become available.</small>
        """
        info = gr.HTML(html)
        return [info, replace_underscore]

    def run(self, p, info, replace_underscore):
        fix_seed(p)

        original_prompt = p.prompt[0] if type(p.prompt) == list else p.prompt
        original_seed = p.seed
        

        all_prompts = [
            generate_prompt(original_prompt, replace_underscore) for _ in range(p.n_iter)
        ]

        all_seeds = [int(p.seed) + (x if p.subseed_strength == 0 else 0) for x in range(len(all_prompts))]

        p.n_iter = math.ceil(len(all_prompts) / p.batch_size)
        p.do_not_save_grid = True

        print(f"Prompt matrix will create {len(all_prompts)} images using a total of {p.n_iter} batches.")

        p.prompt = all_prompts
        p.seed = all_seeds

        p.prompt_for_display = original_prompt
        processed = process_images(p)

        p.prompt = original_prompt
        p.seed = original_seed

        return processed
