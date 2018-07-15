#!/usr/bin/env python3
# -*- coding: utf-8-unix -*-
"""
GigaSt Controller

10bit ADC
- 1024 == 0dBm == k * log10(input)
- 400 ~ -10dBm
- 150 ~ -80dBm

input = 10 ** (1024 / k)
-> 10 ** -3 = 10 ** (1024 / k)
-> -3 == (1024 / k)
-> k = 1024 / -3 = -341.3



"""

import sys
import os
import wx

from argparse import ArgumentParser

from pubsub import pub
from struct import pack, unpack
from serial import Serial
from ucdev.register import Register

import numpy as np

import matplotlib
matplotlib.interactive(True)
matplotlib.use('WXAgg')

from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg as Canvas
from matplotlib.figure import Figure

import logging
log = logging.getLogger(__name__)

# GigaSt v4 command format
GS4_CMD = Register("""
:8 START:24 STEP:8 SAMPLE:8 XPLOT:16
:8 RBW:8 BAND:8 TG:8 ADJUST:8
:8 SG_FREQ:24 SG_BAND:8 DELAY:8 ADCH:8 NV10V:8
""", 0)

######################################################################

def hack_wx():
    """HACK: Enable easier access to GUI from Jupyter"""

    # access parent and children
    wx.Window.p = property(lambda self: self.GetParent())
    wx.Window.c = property(lambda self: self.GetChildren())

    def find_prev_c(self):
        """Return prev sibling"""
        c = self.p.c
        n = c.index(self)
        return c[n - 1] if n > 0 else None
    wx.Window.pc = property(find_prev_c)

    def find_next_c(self):
        """Return next sibling"""
        c = self.p.c
        n = c.index(self)
        return c[n + 1] if len(c) > (n + 1) else None
    wx.Window.nc = property(find_next_c)

######################################################################

class ConfigPanel(wx.Panel):
    def __init__(self, *args, **kw):
        wx.Panel.__init__(self, *args, **kw)

        self.scan_timer = None

        sz = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(sz)

        lb = wx.StaticText(self, -1, "PORT")
        sz.Add(lb, flag=wx.GROW)

        self.cf_port = wx.ComboBox(self, -1, "/dev/ttyUSB0")
        sz.Add(self.cf_port, flag=wx.GROW)

        lb = wx.StaticText(self, -1, "START[KHz]")
        sz.Add(lb, flag=wx.GROW)

        self.cf_start = wx.ComboBox(self, -1, "1395000",
                                    style=wx.TE_RIGHT)
        sz.Add(self.cf_start, flag=wx.GROW)

        lb = wx.StaticText(self, -1, "BAND")
        sz.Add(lb, flag=wx.GROW)

        self.cf_band = wx.ComboBox(self, -1, "1")
        sz.Add(self.cf_band, flag=wx.GROW)

        lb = wx.StaticText(self, -1, "STEP[KHz]")
        sz.Add(lb, flag=wx.GROW)

        self.cf_step = wx.ComboBox(self, -1, "20")
        sz.Add(self.cf_step, flag=wx.GROW)

        lb = wx.StaticText(self, -1, "RBW")
        sz.Add(lb, flag=wx.GROW)

        self.cf_rbw = wx.ComboBox(self, -1, choices=["200KHz", "50KHz"],
                                  value="200KHz",  style=wx.CB_READONLY)
        sz.Add(self.cf_rbw, flag=wx.GROW)

        lb = wx.StaticText(self, -1, "SAMPLE[n]")
        sz.Add(lb, flag=wx.GROW)

        self.cf_sample = wx.ComboBox(self, -1, "1")
        sz.Add(self.cf_sample, flag=wx.GROW)

        lb = wx.StaticText(self, -1, "Adjust[KHz]")
        sz.Add(lb, flag=wx.GROW)

        self.cf_adjust = wx.ComboBox(self, -1, "2")
        sz.Add(self.cf_adjust, flag=wx.GROW)

        lb = wx.StaticText(self, -1, "DELAY[us]")
        sz.Add(lb, flag=wx.GROW)

        self.cf_delay = wx.ComboBox(self, -1, "100")
        sz.Add(self.cf_delay, flag=wx.GROW)

        self.bt_run = wx.Button(self, -1, "RUN", size=(300,200))
        self.bt_run.Bind(wx.EVT_BUTTON, self.do_run)
        sz.Add(self.bt_run, flag=wx.ALIGN_CENTER)

    def do_run(self, event):
        if self.scan_timer:
            self.scan_timer.Stop()
            self.scan_timer = None
            self.Bind(wx.EVT_TIMER, None)
            self.bt_run.SetLabel("RUN")
        else:
            self.scan_timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self.do_scan)
            self.scan_timer.Start(1000)
            self.bt_run.SetLabel("STOP")
 
    def do_scan(self, ev):
        cmd = GS4_CMD()
        cmd.START   = int(int(self.cf_start.GetValue()) / 20)
        cmd.STEP    = int(int(self.cf_step.GetValue()) / 20)
        cmd.SAMPLE  = int(self.cf_sample.GetValue())
        cmd.XPLOT   = 500
        cmd.RBW     = self.cf_rbw.GetSelection()
        cmd.BAND    = int(self.cf_band.GetValue())
        cmd.TG      = 0
        cmd.ADJUST  = int(int(self.cf_adjust.GetValue()) / 20) + 127
        cmd.SG_FREQ = 1900000
        cmd.SG_BAND = 1
        cmd.DELAY   = int(int(self.cf_delay.GetValue()) / 100)
        cmd.ADCH    = 0
        cmd.NV10V   = 0
        
        config = lambda:0
        config.cmd  = cmd
        config.port = self.cf_port.GetValue()

        pub.sendMessage("scan_freq", config=config)

class PlotPanel(wx.Panel):
    def __init__(self, *args, **kw):
        wx.Panel.__init__(self, *args, **kw)

        # data model
        self.model = None

        # plot objects
        self.figure = Figure(None)
        self.axes = self.figure.add_subplot(111)
        self.canvas = Canvas(self, -1, self.figure)

        # make plot resizeable
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.Add(self.canvas, flag=wx.EXPAND, proportion=1)
        self.SetSizer(sizer)

        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)

        # subscribe to various redraw events
        pub.subscribe(self.plot_sp, 'plot_sp')

        # draw initial plot
        self.redraw()

    def on_key(self, ev):
         if ev.GetKeyCode() == wx.WXK_UP:
             pub.sendMessage("freq_zoom_out")
             return

         if ev.GetKeyCode() == wx.WXK_DOWN:
             pub.sendMessage("freq_zoom_in")
             return

         if ev.GetKeyCode() == wx.WXK_LEFT:
             pub.sendMessage("freq_move_up")
             return

         if ev.GetKeyCode() == wx.WXK_RIGHT:
             pub.sendMessage("freq_move_down")
             return

         ev.Skip()

    def redraw(self):
        self.axes.clear()
        if self.model:
            md = self.model
            f0 = md.sp_conf.cmd.START.uint * 20
            f1 = md.sp_conf.cmd.STEP.uint * 20
            nr = md.sp_conf.cmd.XPLOT.uint

            # TODO: Change unit between KHz-MHz-GHz
            f_range = np.linspace(f0 / 1000.0, (f0 + f1 * nr) / 1000.0, nr)

            # NOTE:
            # - This is an empirically derived equation.
            # - My guess is that power P = 10 ** -(2 + 1023 / ADC), which means
            #   full 1023 reading of 10-bit ADC equals to 0dBm = 1mW (10**-3) of power.
            dbm = 10 - 10230 / md.sp_data

            self.axes.plot(f_range, dbm)
            #self.axes.set_xlim(md.xrange)
            self.axes.set_ylim(md.yrange)
            self.axes.grid(True)
        else:
            self.axes.plot([1, 2, 3], [4, 5, 6], 'ro-', picker=5)
        self.canvas.draw()

    def plot_sp(self, model=None):
        self.model = model
        self.redraw()

class MyFrame(wx.Frame):
    def __init__(self, parent=None, *args, **kw):
        wx.Frame.__init__(self, parent, *args, **kw)

        # MenuBar and StatusBar
        mbar = wx.MenuBar()
        self.SetMenuBar(mbar)

        menu = wx.Menu()
        mbar.Append(menu, "&File")

        menu = wx.Menu()
        mbar.Append(menu, "&Help")

        self.status_bar = self.CreateStatusBar()

        # splitter window
        tb = wx.SplitterWindow(self, -1, style=wx.SP_LIVE_UPDATE)
        tb.SetMinimumPaneSize(100)

        lr = wx.SplitterWindow(tb, -1, style=wx.SP_LIVE_UPDATE)
        lr.SetMinimumPaneSize(100)

        # left/right/bottom panel
        self.pa_config = ConfigPanel(lr)
        self.pa_plot   = PlotPanel(lr)
        bp = wx.Panel(tb, -1, style=wx.BORDER_SUNKEN)

        # create 3-pane layout
        tb.SplitHorizontally(lr, bp, -100)
        lr.SplitVertically(self.pa_config, self.pa_plot, 150)

class MyApp(wx.App):
    def __init__(self, ctx, *args, **kw):
        wx.App.__init__(self, *args, **kw)
        top = MyFrame(title="GigaSt Control", size=(800, 600))
        top.Center()
        top.Show()

        self.ctx = ctx
        self.top = top
        self.ctl = AppControl(top)

    def MainLoopDebug(self):
        """Alternative mainloop to embed Jupyter kernel"""
        from ipykernel.kernelapp import IPKernelApp

        kernel = IPKernelApp.instance()
        kernel.initialize(['pythonw', '--matplotlib=wx'])

        # hack to ease debugging
        hack_wx()

        # expose vars
        ns = kernel.shell.user_ns
        ns['kernel'] = kernel
        ns['app'] = self
        ns['md'] = self.ctl.model
        ns['pp'] = self.top.pa_plot

        # invoke jupyter client
        #
        # FIXME:
        # - JupyterQtConsoleApp.launch_instance(argv) doesn't work for some reason.
        # - Need more clean/portable way to start client in background, esp. on Windows.
        # -- Windows has bizzare support of fork-exec multiprocessing on Python
        cfg = kernel.abs_connection_file.replace(os.path.sep, "/")
        cmd = "jupyter-qtconsole --existing %s" % cfg
        os.system("sh -c \"%s &\"" % cmd)

        # start kernel and GUI event loop
        kernel.start()

class AppModel(object):
    def __init__(self):
        # for Spectrum Analyzer mode
        self.sp_conf = None
        self.sp_buff = None
        self.sp_data = None
        self.sp_peak = None

        # for Signal Generator mode
        self.sg_conf = None
        self.sg_buff = None
        self.sg_data = None
        self.sg_peak = None

        # for Tracking Generator mode
        self.tg_conf = None
        self.tg_buff = None
        self.tg_data = None
        self.tg_peak = None

        # plot view configuration
        self.xrange = (0, 10e6)
        self.yrange = (-70.0, 0.0)

class AppControl(object):
    def __init__(self, top):
        self.top = top
        self.model = AppModel()
        self.port = None

        pub.subscribe(self.scan_freq, 'scan_freq')

    def scan_freq(self, config=None):
        # save current setup
        self.model.sp_conf = config

        if self.port and not self.port.closed:
            self.port.close()
        self.port = Serial(port=config.port, baudrate=38400)

        # IO with device
        self.scan_freq_real(config.cmd)

        # notify for redraw
        pub.sendMessage("plot_sp", model=self.model)

    def scan_freq_real(self, cmd):
        # save current setup
        self.model.sp_conf.cmd = cmd

        # send/recv
        self.port.write(cmd.value.bytes)
        self.model.sp_buff = self.port.read(1003)

        # decode buffer
        #
        # NOTE:
        # - ADC part is in big endian, while PEAK freq part is in little endian
        *data, peak_lo, peak_hi, peak_ex = unpack(">500HBBB", self.model.sp_buff)

        self.model.sp_data = np.array(data)
        self.model.sp_peak = peak_ex << 16 | peak_hi << 8 | peak_lo

if __name__ == '__main__' and '__file__' in globals():
    ap = ArgumentParser()
    ap.add_argument('-D', '--debug', default='INFO')
    ap.add_argument('-J', '--jupyter', action='store_true')
    ap.add_argument('args', nargs='*')
    
    # parse args
    ctx = lambda:0
    ctx.opt = ap.parse_args()

    # setup logger
    logging.basicConfig(level=eval('logging.' + ctx.opt.debug))

    app = MyApp(ctx)

    if ctx.opt.jupyter:
        app.MainLoop = app.MainLoopDebug
    app.MainLoop()
