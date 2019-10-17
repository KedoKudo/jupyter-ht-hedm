# jupyter-ht-hedm
Driving HT-HEDM experiment with Bluesky+Ophyd as backend and Jupyter as the front-end.

* On VirtualBeamline testing
=> 10-17-2019: 
    > Tomography passed initial tests with step scan, both tiff and hdf outputs are supported
    > File path will lead to a time out in RE with bps.mv(det.tiff1.file_path, 'XXXXX'). Bluesky may be waiting for read back from (det.tiff1.file_path_exists).
