import numpy as np
import os
from datetime import datetime
from erg_analysis.coordinate.geom2rmlatmlt import geom2rmlatmlt
from common import time, display

from ._downloader import run_iri_profile
from ._getdata import extract_iri_profile_data

def getdata(
        times,
        rmlatmlt,
        res_alt=50, # altitude resolution
        info=True,
):
    """
    Return
    ------
    dict
        * 'times'
        * 'altitude' [m]
        * 'Ne' [/m^3]
        * 'O+' [%]
        * 'N+'
        * 'H+'
        * 'He+'
        * 'O2+'
        * 'NO+'
    """
    max_alt = 2000
    if len(times) != len(rmlatmlt):
        display.error('The lengths of times and rmlatmlt must be same')
        return
    
    if rmlatmlt.ndim != 2 and rmlatmlt.shape[1] != 3:
        display.error('rmlatmlt shape error')
        return
    
    r = rmlatmlt[:, 0]
    mlat = rmlatmlt[:, 1]
    mlt = rmlatmlt[:, 2]

    alt, lat, lon = geom2rmlatmlt(times, r, mlat, mlt, to='geom')

    alt = np.where(alt > max_alt, np.nan, alt)
    lat = np.where(alt > max_alt, np.nan, lat)
    lon = np.where(alt > max_alt, np.nan, lon)
    
    alt *= 1e3 # altitude [m]
    lon = np.fmod(lon + 360, 360) # longitude: [0, 360]

    dt_times = time.convert(times, frm='unix', into='datetime')
    output_filename = '.temporal_iri_profile_output.txt'
    dict_return = {
        'times': [],
        'altitude': [],
        'Ne': [],
        'O+': [],
        'N+': [],
        'H+': [],
        'He+': [],
        'O2+': [],
        'NO+': [],
    }
    vars = ['Ne', 'O+', 'N+', 'H+', 'He+', 'O2+', 'NO+']

    start_time_loop = datetime.now()
    for i in range(len(times)):
        display.progress_bar(i, len(times), start_time_loop)
        dt_times_i = dt_times[i]
        lon_i = lon[i]
        lat_i = lat[i]
        alt_i = alt[i]
        if np.isnan(alt_i):
            continue
        ret = run_iri_profile(
            dt_times_i,
            lon_i,
            lat_i,
            coord_type='geom',
            output_filename=output_filename,
            step_alt=res_alt,
            info=info
        )
        if ret != 0:
            display.warning('run_iri_profile failed')
            return
        dict_data = extract_iri_profile_data(output_filename)
        dict_return['times'].append(times[i])
        alt_data = dict_data['altitude']
        idx_to_get = np.argmin(np.abs(alt_data - alt[i]))
        dict_return['altitude'].append(alt_i)
        for var in vars:
            dict_return[var].append(dict_data[var][idx_to_get])
    
    # list -> ndarray
    for var in dict_return.keys():
        dict_return[var] = np.array(dict_return[var])

    # delete temporal file
    os.remove(output_filename)
    print(f'Deleted temporal file: {output_filename}')
    
    return dict_return
