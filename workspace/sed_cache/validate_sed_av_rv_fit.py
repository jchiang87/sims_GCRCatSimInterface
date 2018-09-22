import os
import h5py
import numpy as np
import healpy
import time

import GCRCatalogs
from GCR import GCRQuery

from desc.sims.GCRCatSimInterface import sed_filter_names_from_catalog
from desc.sims.GCRCatSimInterface import sed_from_galacticus_mags
from lsst.sims.photUtils import BandpassDict
from lsst.sims.photUtils import cache_LSST_seds, getImsimFluxNorm

import argparse

_healpix_list = [10201, 10327, 10328, 10329, 10450,
                 10451, 10452, 10453, 10570, 10571,
                 10572, 10686, 10687]

_healpix_list = [10451]

def do_fitting(cat, component, healpix):

    filter_data = sed_filter_names_from_catalog(cat)
    filter_names = filter_data[component]['filter_name']
    wav_min = filter_data[component]['wav_min']
    wav_width = filter_data[component]['wav_width']

    H0 = cat.cosmology.H0.value
    Om0 = cat.cosmology.Om0

    healpix_query = GCRQuery('healpix_pixel==%d' % healpix)

    qties = cat.get_quantities(list(filter_names) +
                              ['redshift_true', 'galaxy_id'],
                               native_filters=[healpix_query])

    lim = 10000
    with np.errstate(divide='ignore', invalid='ignore'):
        mag_array = np.array([-2.5*np.log10(qties[ff][:lim]) for ff in filter_names])

    (sed_names,
     mag_norms,
     av_arr,
     rv_arr) = sed_from_galacticus_mags(mag_array,
                                        qties['redshift_true'][:lim],
                                        H0, Om0,
                                        wav_min, wav_width)

    return (qties['redshift_true'], qties['galaxy_id'],
            sed_names, mag_norms, av_arr, rv_arr)

if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument('--healpix', type=int, default=None)
    parser.add_argument('--out_dir', type=str, default=None)
    args = parser.parse_args()
    assert args.healpix is not None
    assert args.out_dir is not None
    if not os.path.isdir(args.out_dir):
        os.makedirs(args.out_dir)

    cat = GCRCatalogs.load_catalog('cosmoDC2_v1.0_image')

    out_file_name = os.path.join(args.out_dir,
                                'fit_mags_vs_cosmo_mags_%d.h5' % args.healpix)


    #cache_LSST_seds(wavelen_min=0.0, wavelen_max=3000.0)

    (disk_redshift, disk_id, disk_sed, disk_mag,
     disk_av, disk_rv) = do_fitting(cat, 'disk', args.healpix)

    print("fit disks")

    (bulge_redshift, bulge_id, bulge_sed, bulge_mag,
     bulge_av, bulge_rv) = do_fitting(cat, 'bulge', args.healpix)

    print("fit bulges")

    np.testing.assert_array_equal(disk_id, bulge_id)
    np.testing.assert_array_equal(disk_redshift, bulge_redshift)

    q_list = ['galaxy_id']
    for bp in 'ugrizy':
        q_list.append('Mag_true_%s_lsst_z0' % bp)

    h_query = GCRQuery('healpix_pixels==%' % args.healpix)
    control_qties = cat.get_quantities(q_list, native_filters=[h_query])

    print("got controls")

    np.testing.assert_array_equal(control_qties['galaxy_id'], disk_id)

    bp_dict = BandpassDict.loadTotalBandpassesFromFiles()
    fit_mags = {}
    for bp in 'ugrizy':
        fit_mags[bp] = np.zeros(len(disk_id), dtype=float)

    ax = None
    bx = None
    ccm_w = None

    t_start = time.time()
    for ii in range(len(disk_id)):
        if ii>0 and ii%1000==0:
            duration = (time.time()-t_start)/3600.0
            predicted = len(disk_id)*duration/ii
            print('%d of %d; dur %.2e pred %.2e' %
            (ii, len(disk_id), duration, predicted))

        disk_sed = Sed()
        disk_sed.readSED_flambda(disk_sed[ii])
        fnorm = getImsimFluxNorm(disk_sed, disk_mag[ii])
        disk_sed.multiplyFluxNorm(fnorm)
        if ax is None or not np.array_equal(disk_sed.wavelen, ccm_w):
            ax, bx = disk_sed.setupCCMab()
            ccm_w = np.copy(disk_sed.wavelen)
        disk_sed.addCCMDust(ax, bx, A_v=disk_av[ii], R_v=disk_rv[ii])
        disk_fluxes = bp_dict.fluxListForSed(disk_sed)

        bulge_sed = Sed()
        bulge_sed.readSED_flambda(bulge_sed[ii])
        fnorm = getImsimFluxNorm(bulge_sed, bulge_mag[ii])
        bulge_sed.multiplyFluxNorm(fnorm)
        if ax is None or not np.array_equal(bulge_sed.wavelen, ccm_w):
            ax, bx = bulge_sed.setupCCMab()
            ccm_w = np.copy(bulge_sed.wavelen)
        bulge_sed.addCCMDust(ax, bx, A_v=bulge_av[ii], R_v=bulge_rv[ii])
        bulge_fluxes = bp_dict.fluxListForSed(bulge_sed)

        fluxes = bulge_fluxes + disk_fluxes
        mags = disk_sed.magFromFlux(fluxes)
        for i_bp, bp in enumerate('ugrizy'):
            fit_mags[bp][ii] = mags[i_bp]

    with h5py.File(out_file_name, 'w') as f:
        for bp in 'ugrizy':
            f.create_dataset('fit_%s' % bp, data=fit_mags[bp])
            f.create_dataset('cosmo_%s' % bp,
                             data=control_qties['Mag_true_%s_lsst_z0' % bp])
