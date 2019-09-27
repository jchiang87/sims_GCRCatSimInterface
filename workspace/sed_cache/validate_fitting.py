import os
import numpy as np
import h5py
import lsst.sims.photUtils as photUtils
import GCRCatalogs
from GCR import GCRQuery

import argparse

import multiprocessing

def validate_chunk(galaxy_id, redshift, mag_in,
                   in_dir, healpix,
                   my_lock, output_dict):

    sed_dir = os.environ['SIMS_SED_LIBRARY_DIR']

    tot_bp_dict = photUtils.BandpassDict.loadTotalBandpassesFromFiles()

    fit_file = os.path.join(in_dir, 'sed_fit_%d.h5' % healpix)
    assert os.path.isfile(fit_file)
    with my_lock:
        with h5py.File(fit_file, 'r') as in_file:
            sed_names = in_file['sed_names'][()]
            galaxy_id_fit = in_file['galaxy_id'][()]
            valid = np.in1d(galaxy_id_fit, galaxy_id)
            galaxy_id_fit = galaxy_id_fit[valid]
            np.testing.assert_array_equal(galaxy_id_fit, galaxy_id)

            disk_magnorm = in_file['disk_magnorm'][()][:,valid]
            disk_av = in_file['disk_av'][()][valid]
            disk_rv = in_file['disk_rv'][()][valid]
            disk_sed = in_file['disk_sed'][()][valid]

            bulge_magnorm = in_file['bulge_magnorm'][()][:,valid]
            bulge_av = in_file['bulge_av'][()][valid]
            bulge_rv = in_file['bulge_rv'][()][valid]
            bulge_sed = in_file['bulge_sed'][()][valid]

    sed_names = [name.decode() for name in sed_names]

    local_worst = {}
    ccm_w = None
    dummy_sed = photUtils.Sed()
    for i_obj in range(len(disk_av)):
        for i_bp, bp in enumerate('ugrizy'):
            d_sed = photUtils.Sed()
            fname = os.path.join(sed_dir, sed_names[disk_sed[i_obj]])
            d_sed.readSED_flambda(fname)
            fnorm = photUtils.getImsimFluxNorm(d_sed,
                                               disk_magnorm[i_bp][i_obj])
            d_sed.multiplyFluxNorm(fnorm)
            if ccm_w is None or not np.array_equal(d_sed.wavelen, ccm_w):
                ccm_w = np.copy(d_sed.wavelen)
                ax, bx = d_sed.setupCCM_ab()
            d_sed.addDust(ax, bx, R_v=disk_rv[i_obj], A_v=disk_av[i_obj])
            d_sed.redshiftSED(redshift[i_obj], dimming=True)
            d_flux = d_sed.calcFlux(tot_bp_dict[bp])

            b_sed = photUtils.Sed()
            fname = os.path.join(sed_dir, sed_names[bulge_sed[i_obj]])
            b_sed.readSED_flambda(fname)
            fnorm = photUtils.getImsimFluxNorm(b_sed,
                                               bulge_magnorm[i_bp][i_obj])
            b_sed.multiplyFluxNorm(fnorm)
            if ccm_w is None or not np.array_equal(b_sed.wavelen, ccm_w):
                ccm_w = np.copy(b_sed.wavelen)
                ax, bx = b_sed.setupCCM_ab()
            b_sed.addDust(ax, bx, R_v=bulge_rv[i_obj], A_v=bulge_av[i_obj])
            b_sed.redshiftSED(redshift[i_obj], dimming=True)
            b_flux = b_sed.calcFlux(tot_bp_dict[bp])

            tot_flux = b_flux + d_flux
            true_flux = dummy_sed.fluxFromMag(mag_in[bp][i_obj])
            delta_flux = np.abs(1.0-tot_flux/true_flux)
            if bp not in local_worst or delta_flux>local_worst[bp]:
                local_worst[bp] = delta_flux

    with my_lock:
        for bp in 'ugrizy':
            if bp not in output_dict or local_worst[bp]>output_dict[bp]:
                output_dict[bp] = local_worst[bp]

if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument('--healpix', type=int, default=None)
    parser.add_argument('--in_dir', type=str, default=None)
    parser.add_argument('--seed', type=int, default=None)
    parser.add_argument('--nsamples', type=int, default=1000000)

    args = parser.parse_args()
    if args.healpix is None:
        raise RuntimeError("must specify healpix")
    if args.in_dir is None or not os.path.isdir(args.in_dir):
        raise RuntimeError("invalid in_dir")
    if args.seed is None:
        raise RuntimeError("must specify seed")

    mgr = multiprocessing.Manager()
    my_lock = mgr.Lock()
    output_dict = mgr.dict()

    print('loading catalog')
    cat = GCRCatalogs.load_catalog('cosmoDC2_v1.1.4_image')
    h_query = GCRQuery('healpix_pixel==%d' % args.healpix)
    data = cat.get_quantities(['galaxy_id', 'redshift',
                               'mag_true_u_lsst', 'mag_true_g_lsst',
                               'mag_true_r_lsst', 'mag_true_i_lsst',
                               'mag_true_z_lsst', 'mag_true_y_lsst'],
                              native_filters=[h_query])
    print('got catalog')

    sub_sample = slice(0, 1000)
    mag_in = {}
    for bp in 'ugrizy':
        mag_in[bp] = data['mag_true_%s_lsst' % bp][sub_sample]

    validate_chunk(data['galaxy_id'][sub_sample],
                   data['redshift'][sub_sample],
                   mag_in, args.in_dir, args.healpix,
                   my_lock, output_dict)

    for bp in output_dict.keys():
        print('%s %e' % (bp, output_dict[bp]))
