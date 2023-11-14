# This file is retained in it's slimmest form which handles autorestart for users on
# windows and osx until we have proper autorestart docs for theses oses
# no new features will be added to this file
# issues in this file are to be met with removal, not with fixes.

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from melaniebot import MIN_PYTHON_VERSION
from melaniebot.core import __version__
from melaniebot.setup import load_existing_config

INTERACTIVE_MODE = len(sys.argv) <= 1

INTRO = "==========================\nRed Discord Bot - Launcher\n==========================\n"

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"

PYTHON_OK = sys.version_info >= MIN_PYTHON_VERSION or os.getenv("READTHEDOCS", False)


def is_venv():
    """Return True if the process is in a venv or in a virtualenv."""
    # credit to @calebj
    return hasattr(sys, "real_prefix") or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)


def parse_cli_args():
    parser = argparse.ArgumentParser(description="Melanie - Discord Bot's launcher (V3)", allow_abbrev=False)
    instances = load_existing_config()
    parser.add_argument("instancename", metavar="instancename", type=str, nargs="?", help="The instance to run", choices=list(instances.keys()))
    parser.add_argument("--start", "-s", help="Starts Melanie", action="store_true")
    parser.add_argument("--auto-restart", help="Autorestarts Melanie in case of issues", action="store_true")
    return parser.parse_known_args()


def run_red(selected_instance, autorestart: bool = False, cliflags=None):
    interpreter = sys.executable
    while True:
        print(f"Starting {selected_instance}...")
        cmd_list = [interpreter, "-m", "melaniebot", selected_instance]
        if cliflags:
            cmd_list += cliflags
        status = subprocess.call(cmd_list)
        if not autorestart or status != 26:
            break


def instance_menu():
    instances = load_existing_config()
    if not instances:
        print("No instances found!")
        return None
    counter = 0
    print("Melanie instance menu\n")

    name_num_map = {}
    for name in list(instances.keys()):
        print(f"{counter + 1}. {name}\n")
        name_num_map[str(counter + 1)] = name
        counter += 1

    while True:
        selection = user_choice()
        try:
            selection = int(selection)
        except ValueError:
            print("Invalid input! Please enter a number corresponding to an instance.")
        else:
            if selection not in list(range(1, counter + 1)):
                print("Invalid selection! Please try again")
            else:
                return name_num_map[str(selection)]


def clear_screen():
    if IS_WINDOWS:
        os.system("cls")
    else:
        os.system("clear")


def wait():
    if INTERACTIVE_MODE:
        input("Press enter to continue.")


def user_choice():
    return input("> ").lower().strip()


def main_menu(flags_to_pass):
    if IS_WINDOWS:
        os.system("TITLE Melanie - Discord Bot V3 Launcher")
    clear_screen()
    while True:
        print(INTRO)
        print(f"\033[4mCurrent version:\033[0m {__version__}")
        print("WARNING: The launcher is scheduled for removal at a later date.")
        print("")
        print("1. Run Melanie w/ autorestart in case of issues")
        print("2. Run Melanie")
        print("0. Exit")
        choice = user_choice()
        if choice == "1":
            if instance := instance_menu():
                run_red(instance, autorestart=True, cliflags=flags_to_pass)
            wait()
        elif choice == "2":
            if instance := instance_menu():
                run_red(instance, autorestart=False, cliflags=flags_to_pass)
            wait()
        elif choice == "0":
            break
        clear_screen()


def main():
    args, flags_to_pass = parse_cli_args()
    if not PYTHON_OK:
        print(
            f"Python {'.'.join(map(str, MIN_PYTHON_VERSION))} is required to run Melanie, but you have {sys.version}!",
        )  # Don't make an f-string, these may not exist on the python version being rejected!

    if INTERACTIVE_MODE:
        main_menu(flags_to_pass)
    elif args.start:
        print("WARNING: The launcher is scheduled for removal at a later date.")
        print("Starting Melanie...")
        run_red(args.instancename, autorestart=args.auto_restart, cliflags=flags_to_pass)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Exiting...")
