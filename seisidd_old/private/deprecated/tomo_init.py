#!/usr/bin/env python

# ----- Ipython control config and standard library import ----- #
import os
import socket
import getpass
import bluesky
import ophyd
import apstools
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime


# --- keywords tracking
keywords_vars = {}  # {name: short description}
keywords_func = {}  # {name: short descciption}

from seisidd.utility import print_dict
list_predefined_vars = lambda : print_dict(keywords_vars)
list_predefined_func = lambda : print_dict(keywords_func)


# --- get system info
HOSTNAME = socket.gethostname() or 'localhost'
USERNAME = getpass.getuser() or '6-BM-A user'
keywords_vars['HOSTNAME'] = 'host name'
keywords_vars['USERNAME'] = 'PI/user name'


# --- setup metadata handler
from databroker import Broker
metadata_db = Broker.named("mongodb_config")
keywords_vars['metadata_db'] = 'Default metadata handler'


# --- setup RunEngine
from bluesky import RunEngine
from bluesky.callbacks.best_effort import BestEffortCallback
keywords_func['get_runengine'] = 'Get a bluesky RunEngine'
def get_runengine(db=None):
    """
    Return an instance of RunEngine.  It is recommended to have only
    one RunEngine per session.
    """
    RE = RunEngine({})
    db = metadata_db if db is None else db
    RE.subscribe(db.insert)
    RE.subscribe(BestEffortCallback())
    return RE

RE = get_runengine()
keywords_vars['RE'] = 'Default RunEngine instance'


# --- import utility functions
from utility import load_config
keywords_func['load_config'] = load_config.__doc__


# --- load devices info from yaml file
_devices = load_config('seisidd/config/tomo_devices.yml')


# --- exp safeguard suspender
from bluesky.suspenders import SuspendFloor


# --- define shutter
keywords_func['get_shutter'] = 'Return a connection to a sim/real shutter'
def get_shutter(mode='debug'):
    """
    return
        simulated shutter <-- dryrun, debug
        acutal shutter    <-- production
    """
    import apstools.devices as APS_devices
    aps = APS_devices.ApsMachineParametersDevice(name="APS")

    if mode.lower() in ['debug', 'dryrun']:
        A_shutter = APS_devices.SimulatedApsPssShutterWithStatus(name="A_shutter")
    elif mode.lower() == 'production':
        A_shutter = APS_devices.ApsPssShutterWithStatus(
            _devices['A_shutter'][0],
            _devices['A_shutter'][1],
            name="A_shutter",
        )
        suspend_APS_current = SuspendFloor(aps.current, 2, resume_thresh=10)
        RE.install_suspender(suspend_APS_current)
    else:
        raise ValueError(f"🙉: invalide mode, {mode}")

    return A_shutter

A_shutter = get_shutter(mode='debug')
keywords_vars['A_shutter'] = "shutter instance"
# no scans until A_shutter is open
suspend_A_shutter = None  # place holder
keywords_vars['suspend_A_shutter'] = "no scans until A_shutter is open"


# --- define motor
from ophyd import MotorBundle
from ophyd import Component
from ophyd import EpicsMotor
from ophyd import sim

class TomoStage(MotorBundle):
    #rotation
    preci = Component(EpicsMotor, _devices['tomo_stage']['preci'], name='preci')       
    samX  = Component(EpicsMotor, _devices['tomo_stage']['samX' ], name='samX' )
    ksamX = Component(EpicsMotor, _devices['tomo_stage']['ksamX'], name='ksamX')
    ksamZ = Component(EpicsMotor, _devices['tomo_stage']['ksamZ'], name='ksamZ')
    samY  = Component(EpicsMotor, _devices['tomo_stage']["samY" ], name="samY" )
    

keywords_func['get_motors'] = 'Return a connection to sim/real tomostage motor'
def get_motors(mode="debug"):
    """
    sim motor <-- debug
    aerotech  <-- dryrun, production
    """
    if mode.lower() in ['dryrun', 'production']:
        tomostage = TomoStage(name='tomostage')
    elif mode.lower() == 'debug':
        tomostage = MotorBundle(name="tomostage")
        tomostage.preci = sim.motor
        tomostage.samX  = sim.motor
        tomostage.ksamX = sim.motor
        tomostage.ksamZ = sim.motor
        tomostage.samY  = sim.motor
    else:
        raise ValueError(f"🙉: invalide mode, {mode}")
    return tomostage

tomostage = get_motors(mode='debug')
keywords_vars['tomostage'] = 'sim/real tomo stage'
preci = tomostage.preci
keywords_vars['preci'] = 'rotation control'
samX = tomostage.samX
keywords_vars['samX'] = 'tomo stage x-translation'
ksamX = tomostage.ksamX
keywords_vars['ksamX'] = 'sample translation above rotation'
ksamZ = tomostage.ksamZ
keywords_vars['ksamZ'] = 'sample translation above rotation'
samY = tomostage.samY
keywords_vars['samY'] = 'tomo stage y-translation'


# --- define psofly control 
from ophyd import EpicsSignal
from ophyd import EpicsSignalRO
from ophyd import Device
import bluesky.plan_stubs as bps

class TaxiFlyScanDevice(Device):
    """
    BlueSky Device for APS taxi & fly scans
    
    Some EPICS fly scans at APS are triggered by a pair of 
    EPICS busy records. The busy record is set, which triggers 
    the external controls to do the fly scan and then reset 
    the busy record. 
    
    The first busy is called taxi and is responsible for 
    preparing the hardware to fly. 
    The second busy performs the actual fly scan. 
    In a third (optional) phase, data is collected 
    from hardware and written to a file.
    """
    taxi = Component(EpicsSignal, "taxi", put_complete=True)
    fly = Component(EpicsSignal, "fly", put_complete=True)
    
    def plan(self):
        yield from bps.mv(self.taxi, self.taxi.enum_strs[1])
        yield from bps.mv(self.fly, self.fly.enum_strs[1])

class EnsemblePSOFlyDevice(TaxiFlyScanDevice):
    motor_pv_name = Component(EpicsSignalRO, "motorName")
    start = Component(EpicsSignal, "startPos")
    end = Component(EpicsSignal, "endPos")
    slew_speed = Component(EpicsSignal, "slewSpeed")

    # scan_delta: output a trigger pulse when motor moves this increment
    scan_delta = Component(EpicsSignal, "scanDelta")

    # advanced controls
    delta_time = Component(EpicsSignalRO, "deltaTime")
    # detector_setup_time = Component(EpicsSignal, "detSetupTime")
    # pulse_type = Component(EpicsSignal, "pulseType")

    scan_control = Component(EpicsSignal, "scanControl")

keywords_func['get_fly_motor'] = 'Return a connection to fly IOC control'
def get_fly_motor(mode='debug'):
    """
    sim motor <-- debug
    fly motor <-- dryrun, production
    """
    if mode.lower() == 'debug':
        psofly = sim.flyer1
    elif mode.lower() in ['dryrun', 'production']:
        psofly = EnsemblePSOFlyDevice(_devices['tomo_stage']['psofly'], name="psofly")
    else:
        raise ValueError(f"🙉: invalide mode, {mode}")
    return psofly

psofly = get_fly_motor(mode='debug')
tomostage.psofly = psofly
keywords_vars['psofly'] = 'fly control instance'


# --- define detector
from ophyd   import AreaDetector
from ophyd   import SingleTrigger, EpicsSignalWithRBV
from ophyd   import ADComponent
from ophyd   import PointGreyDetectorCam
from ophyd   import ProcessPlugin
from ophyd   import TIFFPlugin
from ophyd   import HDF5Plugin
from ophyd   import sim
from pathlib import Path
import epics

class PointGreyDetectorCam6IDD(PointGreyDetectorCam):
    """PointGrey Grasshopper3 cam plugin customizations (properties)"""
    auto_exposure_on_off = ADComponent(EpicsSignalWithRBV, "AutoExposureOnOff")
    auto_exposure_auto_mode = ADComponent(EpicsSignalWithRBV, "AutoExposureAutoMode")
    sharpness_on_off = ADComponent(EpicsSignalWithRBV, "SharpnessOnOff")
    sharpness_auto_mode = ADComponent(EpicsSignalWithRBV, "SharpnessAutoMode")
    gamma_on_off = ADComponent(EpicsSignalWithRBV, "GammaOnOff")
    shutter_auto_mode = ADComponent(EpicsSignalWithRBV, "ShutterAutoMode")
    gain_auto_mode = ADComponent(EpicsSignalWithRBV, "GainAutoMode")
    trigger_mode_on_off = ADComponent(EpicsSignalWithRBV, "TriggerModeOnOff")
    trigger_mode_auto_mode = ADComponent(EpicsSignalWithRBV, "TriggerModeAutoMode")
    trigger_delay_on_off = ADComponent(EpicsSignalWithRBV, "TriggerDelayOnOff")
    frame_rate_on_off = ADComponent(EpicsSignalWithRBV, "FrameRateOnOff")
    frame_rate_auto_mode = ADComponent(EpicsSignalWithRBV, "FrameRateAutoMode")

class HDF5Plugin6IDD(HDF5Plugin):
    """AD HDF5 plugin customizations (properties)"""
    xml_file_name = ADComponent(EpicsSignalWithRBV, "XMLFileName")

class PointGreyDetector6IDD(SingleTrigger, AreaDetector):
    """Point Gray area detector used at 6IDD"""
    # cam component
    cam = ADComponent(PointGreyDetectorCam6IDD, "cam1:")
    # proc plugin
    proc1 = ADComponent(ProcessPlugin, suffix="Proc1:")
    # tiff plugin
    tiff1 = ADComponent(TIFFPlugin, suffix="TIFF1:")
    # HDF5 plugin
    hdf1 = ADComponent(HDF5Plugin6IDD, suffix="HDF1:")

keywords_func['get_detector'] = 'Return a connection to the detector'
def get_detector(
    mode='debug', 
    ADPV_prefix=_devices['area_detector']['ADPV_prefix'],
    ):
    """
    sim det  <-- debug
    PG2      <-- dryrun, production
    """
    if mode.lower() == 'debug':
        det = sim.noisy_det
    elif mode.lower() in ['dryrun', 'production']:
        det = PointGreyDetector6IDD(f"{ADPV_prefix}:", name='det')

        # we need to manually setup the PVs to store background and projections
        # separately in a HDF5 archive
        # this is the PV we use as the `SaveDest` attribute
        # check the following page for important information
        # https://github.com/BCDA-APS/use_bluesky/blob/master/notebooks/sandbox/images_darks_flats.ipynb
        #
        epics.caput(f"{ADPV_prefix}:cam1:FrameType.ZRST", "/exchange/data_white_pre")
        epics.caput(f"{ADPV_prefix}:cam1:FrameType.ONST", "/exchange/data")
        epics.caput(f"{ADPV_prefix}:cam1:FrameType.TWST", "/exchange/data_white_post")
        epics.caput(f"{ADPV_prefix}:cam1:FrameType.THST", "/exchange/data_dark")
        # ophyd needs this configuration
        epics.caput(f"{ADPV_prefix}:cam1:FrameType_RBV.ZRST", "/exchange/data_white_pre")
        epics.caput(f"{ADPV_prefix}:cam1:FrameType_RBV.ONST", "/exchange/data")
        epics.caput(f"{ADPV_prefix}:cam1:FrameType_RBV.TWST", "/exchange/data_white_post")
        epics.caput(f"{ADPV_prefix}:cam1:FrameType_RBV.THST", "/exchange/data_dark")
        # set the layout file for cam
        # NOTE: use the __file__ as anchor should resolve the directory issue.
        _current_fp = str(Path(__file__).parent.absolute())
        _attrib_fp = os.path.join(_current_fp, 'config/PG2_attributes.xml')
        det.cam.nd_attributes_file.put(_attrib_fp)
        # set attributes for HDF5 plugin
        _layout_fp = os.path.join(_current_fp, 'config/tomo6bma_layout.xml')
        det.hdf1.xml_file_name.put(_layout_fp)
        # turn off the problematic auto setting in cam
        det.cam.auto_exposure_auto_mode.put(0)  
        det.cam.sharpness_auto_mode.put(0)
        det.cam.gain_auto_mode.put(0)
        det.cam.frame_rate_auto_mode.put(0)
    else:
        raise ValueError(f"🙉: invalide mode, {mode}")

    return det

det = get_detector(mode='debug')
keywords_vars['det'] = 'Area detector instance'


class RuntimeMode():

    def __init__(self):
        self._mode = 'debug'
        self.set(mode='debug')

    def __repr__(self):
        return f"Current runtime mode is set to: {self._mode} ['debug', 'dryrun', 'production']"

    def set(self, mode, config=None):
        """
        (Re)-initialize all devices based on given mode
        simulated devices <-- debug
        actual devices    <-- dryrun, production
        """
        if mode.lower() not in ['debug', 'dryrun', 'production']:
            raise ValueError(f"Unknown mode: {mode}")
        else:
            self._mode = mode
        
        global A_shutter
        global suspend_A_shutter
        global tomostage
        global preci, samX, ksamX, ksamZ, samY 
        global psofly
        global det

        # re-init all tomo related devices
        A_shutter = get_shutter(self._mode)
        tomostage = get_motors(self._mode) 
        preci     = tomostage.preci              
        samX      = tomostage.samX               
        ksamX     = tomostage.ksamX
        ksamZ     = tomostage.ksamZ        
        samY      = tomostage.samY               
        psofly    = get_fly_motor(self._mode)
        det       = get_detector(self._mode)

        # some quick sanity check production mode
        import apstools.devices as APS_devices
        aps = APS_devices.ApsMachineParametersDevice(name="APS")
        suspend_A_shutter = SuspendFloor(A_shutter.pss_state, 1)
        """
        if mode.lower() in ['production']:
            if aps.inUserOperations and (instrument_in_use() in (1, "6-BM-A")) and (not hutch_light_on()):
                
                RE.install_suspender(suspend_A_shutter)
            else:
                raise ValueError("Cannot be in production mode!")
        else:
            pass
        """

        # TODO:
        # initialize all values in the dictionary

mode = RuntimeMode()


# work around for resume motor position
keywords_vars['init_motors_pos'] = 'dict with cached motor position'
init_motors_pos = {
    'samX':  samX.position,
    'samY':  samY.position,
    'ksamX': ksamX.position,
    'ksamZ': ksamZ.position,
    'preci': preci.position,
}

keywords_func['resume_motors_position'] = 'Move motors back to init position'
def resume_motors_position():
    samX.mv( init_motors_pos['samX' ])
    samY.mv( init_motors_pos['samY' ])
    ksamX.mv(init_motors_pos['ksamX' ])
    ksamZ.mv(init_motors_pos['ksamZ' ])
    preci.mv(init_motors_pos['preci'])