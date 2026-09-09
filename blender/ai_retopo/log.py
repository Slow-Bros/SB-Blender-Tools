# SPDX-License-Identifier: GPL-3.0-or-later
"""Konsolen-Log mit dem gemeinsamen Prefix. Kein bpy, damit auch der
Worker-Thread und die Registry es benutzen koennen."""

PREFIX = "[SB-AI-RETOPO]"


def log(message):
    print(f"{PREFIX} {message}")
