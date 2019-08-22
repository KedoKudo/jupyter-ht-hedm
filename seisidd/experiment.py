#!/usr/bin/env python

"""
This module provide high-level class that provides simplified APIs for
conducting different experiment.
"""

import os
import pathlib
import bluesky
import ophyd
import epics
import databroker

from   bluesky.callbacks.best_effort import BestEffortCallback
from   bluesky.suspenders            import SuspendFloor
from   bluesky.simulators            import summarize_plan
from   .motors                       import TomoStage
from   .motors                       import EnsemblePSOFlyDevice
from   .detectors                    import PointGreyDetector6IDD
from   .tomo_plans                   import tomo_scan

class Experiment:
    """
    Generic experiment handler for conducting experiment at 6-ID-D
    """

    def __init__(self, mode='debug'):
        self.RE = bluesky.RunEngine({})
        self.db = databroker.Broker.named("mongodb_config")
        self.RE.subscribe(self.db.insert)
        self.RE.subscribe(BestEffortCallback())

        self._mode   = mode
        from apstools.devices import ApsMachineParametersDevice
        self._aps    = ApsMachineParametersDevice(name="APS")
        self.shutter = Experiment.get_shutter(mode)
        self.suspend_shutter = SuspendFloor(self.shutter.pss_state, 1)

        # monitor APS current
#         self.suspend_APS_current = SuspendFloor(self._aps.current, 2, resume_thresh=10)
#         self.RE.install_suspender(self.suspend_APS_current)

    @staticmethod
    def get_shutter(mode):
        """
        return
            simulated shutter when [dryrun, debug]
            acutal shutter    when [productio]
        """
        if mode.lower() in ['debug', 'dryrun']:
            from apstools.devices import SimulatedApsPssShutterWithStatus
            A_shutter = SimulatedApsPssShutterWithStatus(name="A_shutter")
        elif mode.lower() == 'production':
            from apstools.devices import ApsPssShutterWithStatus
            A_shutter = ApsPssShutterWithStatus(
                "6bmb1:rShtrA:",
                "PA:06BM:STA_A_FES_OPEN_PL",
                name="A_shutter",
            )
        else:
            raise ValueError(f"Invalide mode, {mode}")
        
        return A_shutter

    def list_devices(self):
        """Return all initialized devices"""
        pass

    def list_members(self):
        """Return formated table of all members"""
        pass


class Tomography(Experiment):
    """
    Tomography experiment control for 6-ID-D.
    """

    def __init__(self, mode='debug'):
        Experiment.__init__(self, mode)
        self._mode = mode
        self.tomostage = Tomography.get_tomostage(mode)
        self.psofly = Tomography.get_psofly(mode)
        self.det = Tomography.get_detectors(mode)
        # cache initial motor position
        self.cache_motor_position()
    
    @property
    def mode(self):
        return f"current mode is {self._mode}, available options are ['debug', 'dryrun', 'production']"
    
    @mode.setter
    def mode(self, newmode):
        self._mode = newmode
        self.shutter   = Experiment.get_shutter(self._mode)
        self.tomostage = Tomography.get_tomostage(self._mode)
        self.psofly    = Tomography.get_psofly(self._mode)
        self.det       = Tomography.get_detectors(self._mode)
    
    @property
    def preci(self):
        return self.tomostage.preci.position

    @preci.setter
    def preci(self, newpos):
        self.tomostage.preci.mv(newpos)
    
    @property
    def samX(self):
        return self.tomostage.samX.position

    @samX.setter
    def samX(self, newpos):
        self.tomostage.samX.mv(newpos)
    
    @property
    def ksamX(self):
        return self.tomostage.ksamX.position

    @ksamX.setter
    def ksamX(self, newpos):
        self.tomostage.ksamX.mv(newpos)
    
    @property
    def samY(self):
        return self.tomostage.samY.position

    @samY.setter
    def samY(self, newpos):
        self.tomostage.samY.mv(newpos)
    
    @property
    def ksamZ(self):
        return self.tomostage.ksamZ.position

    @ksamZ.setter
    def ksamZ(self, newpos):
        self.tomostage.ksamZ.mv(newpos)
    
    def cache_motor_position(self):
        self.motor_position_cache = {
            'samX':  self.samX,
            'samY':  self.samY,
            'ksamX': self.ksamX,
            'ksamZ': self.ksamZ,
            'preci': self.preci,
        }
        return self.motor_position_cache
    
    def resume_motor_position(self, position_dict=None):
        position_dict = self.motor_position_cache if position_dict is None else position_dict
        # move motors back
        self.samX  = position_dict['samX' ]  
        self.samY  = position_dict['samY' ]
        self.ksamX = position_dict['ksamX']
        self.ksamZ = position_dict['ksamZ'] 
        self.preci = position_dict['preci'] 

    @staticmethod
    def get_tomostage(mode):
        """
        Return
            simulated motors  <-  ['debug']
            actual motors     <-  ['dryrun', 'proudction']
        """
        if mode.lower() in ['dryrun', 'production']:
            tomostage = TomoStage(name='tomostage')
        elif mode.lower() == 'debug':
            # NOTE:
            #    Moving a synthetic motor will lead to some really strange
            #    issue.  This could be related to the API change in the
            #    synAxis.
            from ophyd import sim
            from ophyd import MotorBundle
            tomostage = MotorBundle(name="tomostage")
            tomostage.preci = sim.SynAxis(name='preci')
            tomostage.samX  = sim.SynAxis(name='samX')
            tomostage.ksamX = sim.SynAxis(name='ksamX')
            tomostage.ksamZ = sim.SynAxis(name='ksamz')
            tomostage.samY  = sim.SynAxis(name='samY')
        else:
            raise ValueError(f"Invalide mode -> {mode}")
        return tomostage

    @staticmethod
    def get_psofly(mode):
        """
        Return
            simulated fly motro  <- ['debug']
            psofly motor         <-  ['dryrun', 'proudction']
        """
        if mode.lower() == 'debug':
            from ophyd import sim
            psofly = sim.flyer1
        elif mode.lower() in ['dryrun', 'production']:
            psofly = EnsemblePSOFlyDevice("6bmpreci:eFly:", name="psofly")
        else:
            raise ValueError(f"Invalide mode, {mode}")
        return psofly

    @staticmethod
    def get_detectors(mode):
        """
        Return 
            simulated detector  <-  ['debug']
            pg detector         <-  ['dryrun', 'proudction']
        """
        if mode.lower() == 'debug':
            from ophyd import sim
            det = sim.noisy_det
        elif mode.lower() in ['dryrun', 'production']:
            det = PointGreyDetector6IDD("1idPG2:", name='det')
            # check the following page for important information
            # https://github.com/BCDA-APS/use_bluesky/blob/master/notebooks/sandbox/images_darks_flats.ipynb
            #
            epics.caput("1idPG2:cam1:FrameType.ZRST", "/exchange/data_white_pre")
            epics.caput("1idPG2:cam1:FrameType.ONST", "/exchange/data")
            epics.caput("1idPG2:cam1:FrameType.TWST", "/exchange/data_white_post")
            epics.caput("1idPG2:cam1:FrameType.THST", "/exchange/data_dark")
            # ophyd need this configuration
            epics.caput("1idPG2:cam1:FrameType_RBV.ZRST", "/exchange/data_white_pre")
            epics.caput("1idPG2:cam1:FrameType_RBV.ONST", "/exchange/data")
            epics.caput("1idPG2:cam1:FrameType_RBV.TWST", "/exchange/data_white_post")
            epics.caput("1idPG2:cam1:FrameType_RBV.THST", "/exchange/data_dark")
            # set the layout file for cam
            _current_fp = str(pathlib.Path(__file__).parent.absolute())
            _attrib_fp = os.path.join(_current_fp, 'config/PG2_attributes.xml')
            _layout_fp = os.path.join(_current_fp, 'config/tomo6bma_layout.xml')
            det.cam.nd_attributes_file.put(_attrib_fp)
            det.hdf1.xml_file_name.put(_layout_fp)
            # turn off the problematic auto setting in cam
            det.cam.auto_exposure_auto_mode.put(0)  
            det.cam.sharpness_auto_mode.put(0)
            det.cam.gain_auto_mode.put(0)
            det.cam.frame_rate_auto_mode.put(0)
        else:
            raise ValueError(f"Invalide mode, {mode}")
        return det

    def dryrun(self, tomo_config):
        """use summarize_plan for quick analysis"""
        return summarize_plan(tomo_scan(self, tomo_config))

    def run(self, tomo_config):
        """run the tomography experiment"""
        return self.RE(tomo_scan(self, tomo_config))


if __name__ == "__main__":
    pass