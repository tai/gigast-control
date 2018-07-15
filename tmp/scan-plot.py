#!/usr/bin/env python3
# -*- coding: utf-8-unix -*-

from __future__ import print_function

from binascii import hexlify
from serial import Serial
from IPython import embed
from ucdev.register import Register

import wx
import sys
import os

CMD = Register("""
:8 START_EX:8 START_HI:8 START_LO:8 STEP:8 SAMPLE:8 XPLOT_HI:8 XPLOT_LO:8
:8 RBW:8 BAND:8 TG:8 ADJUST:8
:8 SG_EX:8 SG_HI:8 SG_LO:8 SG_BAND:8 DELAY:8 ADCH:8 NV10V:8
""", 0)

def gencmd():
    """Control command for GigaSt v4"""
    cmd = CMD()

    # start = N * 20KHz
    cmd.START_EX = 0x00
    cmd.START_HI = 0x01
    cmd.START_LO = 0xF4

    # step = N * 20KHz
    cmd.STEP = 250

    cmd.SAMPLE = 1
    cmd.XPLOT_HI = 1
    cmd.XPLOT_LO = 244

    # 0 = 250KHz, 1 = 50KHz
    cmd.RBW = 0

    cmd.BAND = 1
    cmd.TG = 0

    # 0 in -127..127 range
    cmd.ADJUST = 127

    cmd.SG_EX = 0
    cmd.SG_HI = 0
    cmd.SG_LO = 0
    cmd.SG_BAND = 0

    # delay = N * 100us
    cmd.DELAY = 1

    cmd.ADCH = 0
    cmd.NV10V = 0
    return cmd

def connect(port="/dev/ttyUSB0"):
    return Serial(port=port, baudrate=38400)

def main():
    sp = connect()
    cmd = gencmd()
    sp.write(cmd.value.bytes)
    for i in range(500):
        ret = sp.read(2)
        print(hexlify(ret))
    ret = sp.read(3)
    print(hexlify(ret))

if __name__ == '__main__' and '__file__' in globals():
    main()

