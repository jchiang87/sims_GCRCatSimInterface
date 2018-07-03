import h5py
import tempfile
import os
import numpy as np
import shutil
import time
from lsst.sims.catalogs.db import DBObject
from lsst.sims.catUtils.mixins import ExtraGalacticVariabilityModels
from desc.twinkles import TimeDelayVariability
from . import write_sprinkled_truth_db
from . import get_pointing_htmid

__all__ = ["write_sprinkled_lc"]


class AgnSimulator(ExtraGalacticVariabilityModels, TimeDelayVariability):

    def __init__(self, redshift_arr):
        self._redshift_arr = redshift_arr

    def column_by_name(self, key):
        if key!= 'redshift':
            raise RuntimeError("Do not know how to get column %s" % key)
        return self._redshift_arr


def write_sprinkled_lc(h5_file, total_obs_md,
                       pointing_dir, opsim_db_name,
                       field_ra=55.064, field_dec=-29.783,
                       agn_db=None, yaml_file='proto-dc2_v4.6.1',
                       ra_colname='descDitheredRA',
                       dec_colname='descDitheredDec'):

    (htmid_dict,
     mjd_dict) = get_pointing_htmid(pointing_dir, opsim_db_name,
                                    ra_colname=ra_colname,
                                    dec_colname=dec_colname)

    print('\ngot htmid_dict')

    sql_dir = tempfile.mkdtemp(dir=os.environ['SCRATCH'],
                               prefix='sprinkled_sql_')

    (file_name,
     table_list) = write_sprinkled_truth_db(total_obs_md,
                                            field_ra=field_ra,
                                            field_dec=field_dec,
                                            agn_db=agn_db,
                                            yaml_file=yaml_file,
                                            out_dir=sql_dir)

    print('\nwrote db %s' % file_name)

    db = DBObject(file_name, driver='sqlite')

    query = 'SELECT DISTINCT htmid FROM zpoint WHERE is_agn=1'
    dtype = np.dtype([('htmid', int)])

    results = db.execute_arbitrary(query, dtype=dtype)

    object_htmid = results['htmid']

    agn_dtype = np.dtype([('uniqueId', int), ('galaxy_id', int),
                          ('ra', float), ('dec', float),
                          ('redshift', float), ('sed', str, 500),
                          ('magnorm', float), ('varParamStr', str, 500)])

    agn_base_query = 'SELECT uniqueId, galaxy_id, '
    agn_base_query += 'raJ2000, decJ2000, '
    agn_base_query += 'redshift, sedFilepath, '
    agn_base_query += 'magNorm, varParamStr '
    agn_base_query += 'FROM zpoint '

    n_floats = 0
    with h5py.File(h5_file, 'w') as out_file:
        for htmid_dex, htmid in enumerate(object_htmid):
            mjd_arr = []
            t_start = time.time()
            for obshistid in htmid_dict:
                is_contained = False
                for bounds in htmid_dict[obshistid]:
                    if htmid<=bounds[1] and htmid>=bounds[0]:
                        is_contained = True
                        break
                mjd_arr.append(mjd_dict[obshistid])
            mjd_arr = np.sort(np.array(mjd_arr))
            duration = time.time()-t_start
            print('made mjd_arr in %e seconds' % duration)
            if len(mjd_arr) == 0:
                continue

            agn_query = agn_base_query + 'WHERE htmid=%d and is_agn=1' % htmid

            agn_results = db.execute_arbitrary(agn_query, dtype=agn_dtype)

            agn_simulator = AgnSimulator(agn_results['redshift'])

            dmag = agn_simulator.applyVariability(agn_results['varParamStr'],
                                                  expmjd=mjd_arr)

            dmag_name = 'agn_dmag_%d' % htmid_dex
            out_file.create_dataset(dmag_name, data = dmag.transpose(1,0,2))

            n_floats += len(dmag.flatten())

    print('n_floats %d' % n_floats)

    del db
    for file_name in os.listdir(sql_dir):
        os.unlink(os.path.join(sql_dir, file_name))
    shutil.rmtree(sql_dir)
