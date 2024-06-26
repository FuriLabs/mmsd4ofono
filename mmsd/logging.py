# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 Bardia Moshiri <fakeshell@bardia.tech>

from inspect import currentframe

def mmsd_print(message, verbose):
    if not verbose:
        return

    frame = currentframe()
    caller_frame = frame.f_back
    if 'self' in caller_frame.f_locals:
        cls_name = caller_frame.f_locals['self'].__class__.__name__
        func_name = caller_frame.f_code.co_name
        full_message = f"{cls_name}.{func_name}: {message}"
    else:
        func_name = caller_frame.f_code.co_name
        full_message = f"{func_name}: {message}"

    print(full_message)
