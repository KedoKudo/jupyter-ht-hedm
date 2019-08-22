#!/usr/bin/env python

"""
Predefined bluesky scan plans
"""

import numpy                 as np
import bluesky.plans         as bp
import bluesky.preprocessors as bpp
import bluesky.plan_stubs    as bps

from .utility            import load_config

#@bpp.run_decorator()
def collect_white_field(experiment, cfg_tomo, atfront=True):
    """
    Collect white/flat field images by moving the sample out of the FOV
    """
    # unpack devices
    det = experiment.det
    tomostage = experiment.tomostage

    # move sample out of the way
    _x = cfg_tomo['fronte_white_ksamX'] if atfront else cfg_tomo['back_white_ksamX']
    _z = cfg_tomo['fronte_white_ksamZ'] if atfront else cfg_tomo['back_white_ksamZ']
    yield from bps.mv(tomostage.ksamX, _x)
    yield from bps.mv(tomostage.ksamZ, _z)

    # setup detector
    yield from bps.mv(det.hdf1.nd_array_port, 'PROC1')
    yield from bps.mv(det.tiff1.nd_array_port, 'PROC1') 
    yield from bps.mv(det.proc1.enable, 1)
    yield from bps.mv(det.proc1.reset_filter, 1)
    yield from bps.mv(det.proc1.num_filter, cfg_tomo['n_frames'])
    yield from bps.mv(det.cam.trigger_mode, "Internal")
    yield from bps.mv(det.cam.image_mode, "Multiple")
    yield from bps.mv(det.cam.num_images, cfg_tomo['n_frames']*cfg_tomo['n_white'])
    yield from bps.trigger_and_read([det])

    # move sample back to FOV
    # NOTE:
    # not sure is this will work or not...
    yield from bps.mv(tomostage.ksamX, cfg_tomo['initial_ksamX'])
    yield from bps.mv(tomostage.ksamZ, cfg_tomo['initial_ksamZ'])


#@bpp.run_decorator()
def collect_dark_field(experiment, cfg_tomo):
    """
    Collect dark field images by close the shutter
    """
    det = experiment.det

    yield from bps.mv(det.hdf1.nd_array_port, 'PROC1')
    yield from bps.mv(det.tiff1.nd_array_port, 'PROC1') 
    yield from bps.mv(det.proc1.enable, 1)
    yield from bps.mv(det.proc1.reset_filter, 1)
    yield from bps.mv(det.proc1.num_filter, cfg_tomo['n_frames'])
    yield from bps.mv(det.cam.trigger_mode, "Internal")
    yield from bps.mv(det.cam.image_mode, "Multiple")
    yield from bps.mv(det.cam.num_images, cfg_tomo['n_frames']*cfg_tomo['n_dark'])
    yield from bps.trigger_and_read([det])


#@bpp.run_decorator()
def step_scan(experiment, cfg_tomo):
    """
    Collect projects with step motion
    """
    # unpack devices
    det = experiment.det
    tomostage = experiment.tomostage

    yield from bps.mv(det.hdf1.nd_array_port, 'PROC1')
    yield from bps.mv(det.tiff1.nd_array_port, 'PROC1') 
    yield from bps.mv(det.proc1.enable, 1)
    yield from bps.mv(det.proc1.reset_filter, 1)
    yield from bps.mv(det.proc1.num_filter, cfg_tomo['n_frames'])

    angs = np.arange(
        cfg_tomo['omega_start'], 
        cfg_tomo['omega_end']+cfg_tomo['omega_step']/2,
        cfg_tomo['omega_step'],
    )
    for ang in angs:
        yield from bps.checkpoint()
        yield from bps.mv(tomostage.preci, ang)
        yield from bps.trigger_and_read([det])


#@bpp.run_decorator()
def fly_scan(experiment, cfg_tomo):
    """
    Collect projections with fly motion
    """
    det = experiment.det
    psofly = experiment.psofly
    
    yield from bps.mv(det.hdf1.nd_array_port, 'PG1')
    yield from bps.mv(det.tiff1.nd_array_port, 'PG1')

    # we are assuming that the global psofly is available
    yield from bps.mv(
        psofly.start,           cfg_tomo['omega_start'],
        psofly.end,             cfg_tomo['omega_end'],
        psofly.scan_delta,      abs(cfg_tomo['omega_step']),
        psofly.slew_speed,      cfg_tomo['slew_speed'],
    )
    # taxi
    yield from bps.mv(psofly.taxi, "Taxi")
    yield from bps.mv(
        det.cam.num_images, cfg_tomo['n_projections'],
        det.cam.trigger_mode, "Overlapped",
    )
    # start the fly scan
    yield from bps.trigger(det, group='fly')
    yield from bps.abs_set(psofly.fly, "Fly", group='fly')
    yield from bps.wait(group='fly')


def tomo_scan(experiment, cfg):
    """
    Tomography scan plan based on given configuration
    """
    # unpack devices
    det = experiment.det
    tomostage = experiment.tomostage
    shutter = experiment.shutter
    shutter_suspender = experiment.suspend_shutter
    
    cfg = load_config(cfg) if type(cfg) != dict else cfg

    # update the cached motor position in the dict in case exp goes wrong
    _cahed_position = experiment.cache_motor_position()

    # step 0: preparation
    acquire_time   = cfg['tomo']['acquire_time']
    n_white        = cfg['tomo']['n_white']
    n_dark         = cfg['tomo']['n_dark']
    angs = np.arange(
        cfg['tomo']['omega_start'], 
        cfg['tomo']['omega_end']+cfg['tomo']['omega_step']/2,
        cfg['tomo']['omega_step'],
    )
    n_projections = len(angs)
    cfg['tomo']['n_projections'] = n_projections
    total_images  = n_white + n_projections + n_white + n_dark
    fp = cfg['output']['filepath']
    fn = cfg['output']['fileprefix']
    
    # calculate slew speed for fly scan
    # https://github.com/decarlof/tomo2bm/blob/master/flir/libs/aps2bm_lib.py
    # TODO: considering blue pixels, use 2BM code as ref
    if cfg['tomo']['type'].lower() == 'fly':
        scan_time = (acquire_time+cfg['tomo']['readout_time'])*n_projections
        slew_speed = (angs.max() - angs.min())/scan_time
        cfg['tomo']['slew_speed'] = slew_speed
    
    # need to make sure that the sample out position is the same for both front and back
    x0, z0 = tomostage.ksamX.position, tomostage.ksamZ.position
    dfx, dfz = cfg['tomo']['sample_out_position']['samX'], cfg['tomo']['sample_out_position']['samZ']
    rotang = np.radians(cfg['tomo']['omega_end']-cfg['tomo']['omega_start'])
    rotm = np.array([[ np.cos(rotang), np.sin(rotang)],
                     [-np.sin(rotang), np.cos(rotang)]])
    dbxz = np.dot(rotm, np.array([dfx, dfz]))
    dbx = dbxz[0] if abs(dbxz[0]) > 1e-8 else 0.0
    dbz = dbxz[1] if abs(dbxz[1]) > 1e-8 else 0.0
    # now put the value to dict
    cfg['tomo']['initial_ksamX'] = x0
    cfg['tomo']['initial_ksamZ'] = z0
    cfg['tomo']['fronte_white_ksamX'] = x0 + dfx
    cfg['tomo']['fronte_white_ksamZ'] = z0 + dfz
    cfg['tomo']['back_white_ksamX'] = x0 + dbx
    cfg['tomo']['back_white_ksamZ'] = z0 + dbz
    
    @bpp.run_decorator()
    @bpp.stage_decorator([det])
    def scan_closure():
        # open shutter for beam
        yield from bps.mv(shutter, 'open')
        yield from bps.install_suspender(shutter_suspender)
        
        # config output
        for me in [det.tiff1, det.hdf1]:
            yield from bps.mv(me.file_path, fp)
            yield from bps.mv(me.file_name, fn)
            yield from bps.mv(me.file_write_mode, 2)
            yield from bps.mv(me.num_capture, total_images)
            yield from bps.mv(me.file_template, ".".join([r"%s%s_%06d",cfg['output']['type'].lower()]))    

        if cfg['output']['type'] in ['tif', 'tiff']:
            yield from bps.mv(det.tiff1.enable, 1)
            yield from bps.mv(det.tiff1.capture, 1)
            yield from bps.mv(det.hdf1.enable, 0)
        elif cfg['output']['type'] in ['hdf', 'hdf1', 'hdf5']:
            yield from bps.mv(det.tiff1.enable, 0)
            yield from bps.mv(det.hdf1.enable, 1)
            yield from bps.mv(det.hdf1.capture, 1)
        else:
            raise ValueError(f"Unsupported output type {cfg['output']['type']}")

        # collect front white field
        yield from bps.mv(det.cam.frame_type, 0)  # for HDF5 dxchange data structure
        yield from collect_white_field(experiment, cfg['tomo'], atfront=True)

        # collect projections
        yield from bps.mv(det.cam.frame_type, 1)  # for HDF5 dxchange data structure
        if cfg['tomo']['type'].lower() == 'step':
            yield from step_scan(experiment, cfg['tomo'])
        elif cfg['tomo']['type'].lower() == 'fly':
            yield from fly_scan(experiment, cfg['tomo'])
        else:
            raise ValueError(f"Unsupported scan type: {cfg['tomo']['type']}")

        # collect back white field
        yield from bps.mv(det.cam.frame_type, 2)  # for HDF5 dxchange data structure
        yield from collect_white_field(experiment, cfg['tomo'], atfront=False)

        # collect back dark field
        yield from bps.mv(det.cam.frame_type, 3)  # for HDF5 dxchange data structure
        yield from bps.remove_suspender(shutter_suspender)
        yield from bps.mv(shutter, "close")
        yield from collect_dark_field(experiment, cfg['tomo'])

    return (yield from scan_closure())
