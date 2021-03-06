"""
Code to write instance catalogs for phosim and imsim using the gcr-catalogs
galaxy catalogs.
"""
from __future__ import with_statement
import os
import copy
import subprocess
from collections import namedtuple
import numpy as np
import time

from lsst.utils import getPackageDir
from lsst.sims.photUtils import PhotometricParameters
from lsst.sims.photUtils import BandpassDict
from lsst.sims.catalogs.definitions import parallelCatalogWriter
from lsst.sims.catalogs.decorators import cached, compound
from lsst.sims.catUtils.mixins import ParametrizedLightCurveMixin
from desc.sims.GCRCatSimInterface import DC2StarObj
from lsst.sims.catUtils.exampleCatalogDefinitions import \
    PhoSimCatalogPoint, DefaultPhoSimHeaderMap
from lsst.sims.catUtils.mixins import VariabilityStars
from lsst.sims.catUtils.utils import ObservationMetaDataGenerator
from lsst.sims.utils import arcsecFromRadians, _getRotSkyPos
from . import PhoSimDESCQA, PhoSimDESCQA_AGN
from . import TruthPhoSimDESCQA, SprinklerTruthCatMixin
from . import SubCatalogMixin
from . import bulgeDESCQAObject_protoDC2 as bulgeDESCQAObject, \
    diskDESCQAObject_protoDC2 as diskDESCQAObject, \
    knotsDESCQAObject_protoDC2 as knotsDESCQAObject, \
    agnDESCQAObject_protoDC2 as agnDESCQAObject
from .Variability import ExtraGalacticVariabilityModels

try:
    import desc.sims.GCRCatSimInterface.TwinklesClasses.sprinklerCompound_DC2 as sprinklerDESCQACompoundObject
    import desc.sims.GCRCatSimInterface.TwinklesClasses.TwinklesCatalogZPoint_DC2 as DESCQACat_Twinkles
    import desc.sims.GCRCatSimInterface.TwinklesClasses.TwinklesCompoundInstanceCatalog_DC2 as twinklesDESCQACompoundObject
    from desc.sims.GCRCatSimInterface.TwinklesClasses import twinkles_spec_map
    HAS_TWINKLES = True
except ImportError:
    HAS_TWINKLES = False

from . import DC2PhosimCatalogSN, SNeDBObject
from . import hostImage

__all__ = ['InstanceCatalogWriter', 'make_instcat_header', 'get_obs_md',
           'snphosimcat']


# Global `numpy.dtype` instance to define the types
# in the csv files being read
SNDTYPESR1p1 = np.dtype([('snid_in', int),
                         ('x0_in', float),
                         ('t0_in', float),
                         ('x1_in', float),
                         ('c_in', float),
                         ('z_in', float),
                         ('snra_in', float),
                         ('sndec_in', float)])

def snphosimcat(fname, obs_metadata, objectIDtype, sedRootDir,
                idColKey='snid_in'):
    """convenience function for writing out phosim instance catalogs for
    different SN populations in DC2 Run 1.1 that have been serialized to
    csv files.

    Parameters:
    -----------
    fname : string
        absolute path to the sqlite database containing supernova parameters
    obs_metadata: instance of `lsst.sims.utils.ObservationMetaData`
	observation metadata describing the observation
    objectIDtype : int
        A unique integer identifying this class of astrophysical object as used
        in lsst.sims.catalogs.db.CatalogDBObject
    sedRootDir : string
        root directory for writing spectra corresponding to pointings. The spectra
        will be written to the directory `sedRootDir/Dynamic/`
    dtype : instance of `numpy.dtype`
        tuples describing the variables and types in the csv files.


    Returns
    -------
    returns an instance of a Phosim Instance catalogs with appropriate
    parameters set for the objects in the file and the obs_metadata.
    """
    dbobj = SNeDBObject(fname, table='sne_params', driver='sqlite')
    dbobj.raColName = 'snra_in'
    dbobj.decColName = 'sndec_in'
    dbobj.objectTypeId = objectIDtype
    cat = DC2PhosimCatalogSN(db_obj=dbobj, obs_metadata=obs_metadata)
    cat.surveyStartDate = 0.
    cat.maxz = 1.4 # increasing max redshift
    cat.maxTimeSNVisible = 150.0 # increasing for high z SN
    cat.phoSimHeaderMap = DefaultPhoSimHeaderMap
    cat.writeSedFile = True

    # This means that the the spectra written by phosim will
    # go to `spectra_files/Dynamic/specFileSN_*
    # Note: you want DC2PhosimCatalogSN.sep to be part of this prefix
    # string.
    # We can arrange for the phosim output to just read the string
    # without directories or something else
    spectradir = os.path.join(sedRootDir, 'Dynamic')
    os.makedirs(spectradir, exist_ok=True)

    cat.sn_sedfile_prefix = os.path.join(spectradir, 'specFileSN_')
    return cat

class InstanceCatalogWriter(object):
    """
    Class to write instance catalogs for PhoSim and imSim using
    galaxies accessed via the gcr-catalogs interface.
    """
    def __init__(self, opsimdb, descqa_catalog, dither=True,
                 min_mag=10, minsource=100, proper_motion=False,
                 protoDC2_ra=0, protoDC2_dec=0,
                 star_db_name = None,
                 sed_lookup_dir=None,
                 agn_db_name=None, agn_threads=1, sn_db_name=None,
                 sprinkler=False, host_image_dir=None,
                 host_data_dir=None, config_dict=None,
                 gzip_threads=3, objects_to_skip=()):
        """
        Parameters
        ----------
        opsimdb: str
            OpSim db filename.
        descqa_catalog: str
            Name of the DESCQA galaxy catalog.
        dither: bool [True]
            Flag to enable the dithering included in the opsim db file.
        min_mag: float [10]
            Minimum value of the star magnitude at 500nm to include.
        minsource: int [100]
            Minimum number of objects for phosim.py to simulate a chip.
        proper_motion: bool [True]
            Flag to enable application of proper motion to stars.
        protoDC2_ra: float [0]
            Desired RA (J2000 degrees) of protoDC2 center.
        protoDC2_dec: float [0]
            Desired Dec (J2000 degrees) of protoDC2 center.
        star_db_name: str [None]
            Filename of the database containing stellar sources
        sed_lookup_dir: str [None]
            Directory where the SED lookup tables reside.
        agn_db_name: str [None]
            Filename of the agn parameter sqlite db file.
        agn_threads: int [1]
            Number of threads to use when simulating AGN variability
        sn_db_name: str [None]
            Filename of the supernova parameter sqlite db file.
        sprinkler: bool [False]
            Flag to enable the Sprinkler.
        host_image_dir: string
            The location of the FITS images of lensed AGN/SNe hosts produced by generate_lensed_hosts_***.py
        host_data_dir: string
            Location of csv file of lensed host data created by the sprinkler
        gzip_threads: int
            The number of gzip jobs that can be started in parallel after
            catalogs are written (default=3)
        objects_to_skip: set-like or list-like [()]
            Collection of object types to skip, e.g., stars, knots, bulges, disks, sne, agn
        """
        self.t_start = time.time()
        if not os.path.exists(opsimdb):
            raise RuntimeError('%s does not exist' % opsimdb)

        self.gzip_threads = gzip_threads

        # load the data for the parametrized light
        # curve stellar variability model into a
        # global cache
        plc = ParametrizedLightCurveMixin()
        plc.load_parametrized_light_curves()

        self.config_dict = config_dict if config_dict is not None else {}

        self.descqa_catalog = descqa_catalog
        self.dither = dither
        self.min_mag = min_mag
        self.minsource = minsource
        self.proper_motion = proper_motion
        self.protoDC2_ra = protoDC2_ra
        self.protoDC2_dec = protoDC2_dec

        self.phot_params = PhotometricParameters(nexp=1, exptime=30)
        self.bp_dict = BandpassDict.loadTotalBandpassesFromFiles()

        self.obs_gen = ObservationMetaDataGenerator(database=opsimdb,
                                                    driver='sqlite')

        if star_db_name is None:
            raise IOError("Need to specify star_db_name")

        if not os.path.isfile(star_db_name):
            raise IOError("%s is not a file\n" % star_db_name
                          + "(This is what you specified for star_db_name")

        self.star_db = DC2StarObj(database=star_db_name,
                                  driver='sqlite')

        self.sprinkler = sprinkler
        if self.sprinkler and not HAS_TWINKLES:
            raise RuntimeError("You are trying to enable the sprinkler; "
                               "but Twinkles cannot be imported")

        if not os.path.isdir(sed_lookup_dir):
            raise IOError("\n%s\nis not a dir" % sed_lookup_dir)
        self.sed_lookup_dir = sed_lookup_dir

        self._agn_threads = agn_threads
        if agn_db_name is not None:
            if os.path.exists(agn_db_name):
                self.agn_db_name = agn_db_name
            else:
                raise IOError("Path to Proto DC2 AGN database does not exist.")
        else:
            self.agn_db_name = None

        self.sn_db_name = None
        if sn_db_name is not None:
            if os.path.isfile(sn_db_name):
                self.sn_db_name = sn_db_name
            else:
                raise IOError("%s is not a file" % sn_db_name)

        if host_image_dir is None and self.sprinkler is not False:
            raise IOError("Need to specify the name of the host image directory.")
        elif self.sprinkler is not False:
            if os.path.exists(host_image_dir):
                self.host_image_dir = host_image_dir
            else:
                raise IOError("Path to host image directory"
                              + "\n\n%s\n\n" % host_image_dir
                              + "does not exist.")

        if host_data_dir is None and self.sprinkler is not False:
            raise IOError("Need to specify the name of the host data directory.")
        elif self.sprinkler is not False:
            if os.path.exists(host_data_dir):
                self.host_data_dir = host_data_dir
            else:
                raise IOError("Path to host data directory does not exist.\n\n",
                              "%s\n\n" % host_data_dir)

        self.instcats = get_instance_catalogs()
        object_types = 'stars knots bulges disks sprinkled hosts sne agn'.split()
        if any([_ not in object_types for _ in objects_to_skip]):
            raise RuntimeError(f'objects_to_skip ({objects_to_skip}) '
                               'contains invalid object types')
        self.do_obj_type = {_: _ not in objects_to_skip for _ in object_types}

    def write_catalog(self, obsHistID, out_dir=None, fov=2, status_dir=None,
                      pickup_file=None, skip_tarball=False, region_bounds=None):
        """
        Write the instance catalog for the specified obsHistID.

        Parameters
        ----------
        obsHistID: int
            ID of the desired visit.
        out_dir: str [None]
            Output directory.  It will be created if it doesn't already exist.
            This is actually a parent directory.  The InstanceCatalog will be
            written to 'out_dir/%.8d' % obsHistID
        fov: float [2.]
            Field-of-view angular radius in degrees.  2 degrees will cover
            the LSST focal plane.
        status_dir: str [None]
            The directory in which to write the log file recording this job's
            progress.
        pickup_file: str [None]
            The path to an aborted log file (the file written to status_dir).
            This job will resume where that one left off, only simulating
            sub-catalogs that did not complete.
        skip_tarball: bool [False]
            Flag to skip making a tarball out of the instance catalog folder.
        region_bounds: (float, float, float, float) [None]
            Additional bounds on ra, dec to apply, specified by
            `(ra_min, ra_max, dec_min, dec_max)`.  If None, then no
            additional selection will be applied.
        """

        print('process %d doing %d' % (os.getpid(), obsHistID))

        if out_dir is None:
            raise RuntimeError("must specify out_dir")

        full_out_dir = os.path.join(out_dir, '%.8d' % obsHistID)
        tar_name = os.path.join(out_dir, '%.8d.tar' % obsHistID)

        if pickup_file is not None and os.path.isfile(pickup_file):
            with open(pickup_file, 'r') as in_file:
                for line in in_file:
                    if 'wrote star' in line:
                        self.do_obj_type['stars'] = False
                    if 'wrote knot' in line:
                        self.do_obj_type['knots'] = False
                    if 'wrote bulge' in line:
                        self.do_obj_type['bulges'] = False
                    if 'wrote disk' in line:
                        self.do_obj_type['disks'] = False
                    if 'wrote galaxy catalogs with sprinkling' in line:
                        self.do_obj_type['sprinkled'] = False
                    if 'wrote lensing host' in line:
                        self.do_obj_type['hosts'] = False
                    if 'wrote SNe' in line:
                        self.do_obj_type['sne'] = False
                    if 'wrote agn' in line:
                        self.do_obj_type['agn'] = False

        if not os.path.exists(full_out_dir):
            os.makedirs(full_out_dir)

        has_status_file = False
        if status_dir is not None:
            if not os.path.exists(status_dir):
                os.makedirs(status_dir)
            status_file = os.path.join(status_dir, 'job_log_%.8d.txt' % obsHistID)
            if os.path.exists(status_file):
                os.unlink(status_file)
            has_status_file = True

            with open(status_file, 'a') as out_file:
                out_file.write('writing %d\n' % (obsHistID))
                for kk in self.config_dict:
                    out_file.write('%s: %s\n' % (kk, self.config_dict[kk]))

        obs_md = get_obs_md(self.obs_gen, obsHistID, fov, dither=self.dither)

        if obs_md is None:
            return

        if region_bounds is not None:
            obs_md.radec_bounds = region_bounds

        ExtraGalacticVariabilityModels.filters_to_simulate.clear()
        ExtraGalacticVariabilityModels.filters_to_simulate.extend(obs_md.bandpass)

        if has_status_file:
            with open(status_file, 'a') as out_file:
                out_file.write('got obs_md in %e hours\n' %
                               ((time.time()-self.t_start)/3600.0))

        # Add directory for writing the GLSN spectra to
        glsn_spectra_dir = str(os.path.join(full_out_dir, 'Dynamic'))
        os.makedirs(glsn_spectra_dir, exist_ok=True)

        if HAS_TWINKLES:
            twinkles_spec_map.subdir_map['(^specFileGLSN)'] = 'Dynamic'
            # Ensure that the directory for GLSN spectra is created

        phosim_cat_name = 'phosim_cat_%d.txt' % obsHistID
        star_name = 'star_cat_%d.txt' % obsHistID
        bright_star_name = 'bright_stars_%d.txt' % obsHistID
        gal_name = 'gal_cat_%d.txt' % obsHistID
        knots_name = 'knots_cat_%d.txt' % obsHistID
        # keep track of all of the non-supernova InstanceCatalogs that
        # have been written so that we can remember to includeobj them
        # in the PhoSim catalog
        written_catalog_names = []
        sprinkled_host_name = 'spr_hosts_%d.txt' % obsHistID

        if self.do_obj_type['stars']:
            star_cat = self.instcats.StarInstCat(self.star_db, obs_metadata=obs_md)
            star_cat.min_mag = self.min_mag
            star_cat.photParams = self.phot_params
            star_cat.lsstBandpassDict = self.bp_dict
            star_cat.disable_proper_motion = not self.proper_motion

            bright_cat \
                = self.instcats.BrightStarInstCat(self.star_db, obs_metadata=obs_md,
                                                  cannot_be_null=['isBright'])
            bright_cat.min_mag = self.min_mag
            bright_cat.photParams = self.phot_params
            bright_cat.lsstBandpassDict = self.bp_dict

            cat_dict = {os.path.join(full_out_dir, star_name): star_cat,
                        os.path.join(full_out_dir, bright_star_name): bright_cat}
            parallelCatalogWriter(cat_dict, chunk_size=50000, write_header=False)
            written_catalog_names.append(star_name)

            if has_status_file:
                with open(status_file, 'a') as out_file:
                    duration = (time.time()-self.t_start)/3600.0
                    out_file.write('%d wrote star catalog after %.3e hrs\n' %
                                   (obsHistID, duration))

        if 'knots' in self.descqa_catalog and self.do_obj_type['knots']:
            knots_db =  knotsDESCQAObject(self.descqa_catalog)
            knots_db.field_ra = self.protoDC2_ra
            knots_db.field_dec = self.protoDC2_dec
            cat = self.instcats.DESCQACat(knots_db, obs_metadata=obs_md,
                                          cannot_be_null=['hasKnots'])
            cat.sed_lookup_dir = self.sed_lookup_dir
            cat.photParams = self.phot_params
            cat.lsstBandpassDict = self.bp_dict
            cat.write_catalog(os.path.join(full_out_dir, knots_name), chunk_size=5000,
                              write_header=False)
            written_catalog_names.append(knots_name)
            del cat
            del knots_db
            if has_status_file:
                with open(status_file, 'a') as out_file:
                    duration = (time.time()-self.t_start)/3600.0
                    out_file.write('%d wrote knots catalog after %.3e hrs\n' %
                                   (obsHistID, duration))
        elif self.do_obj_type['knots']:
            # Creating empty knots component
            subprocess.check_call('cd %(full_out_dir)s; touch %(knots_name)s' % locals(), shell=True)


        if self.sprinkler is False:

            if self.do_obj_type['bulges']:
                bulge_db = bulgeDESCQAObject(self.descqa_catalog)
                bulge_db.field_ra = self.protoDC2_ra
                bulge_db.field_dec = self.protoDC2_dec
                cat = self.instcats.DESCQACat(bulge_db, obs_metadata=obs_md,
                                              cannot_be_null=['hasBulge', 'magNorm'])
                cat_name = 'bulge_'+gal_name
                cat.sed_lookup_dir = self.sed_lookup_dir
                cat.lsstBandpassDict = self.bp_dict
                cat.photParams = self.phot_params
                cat.write_catalog(os.path.join(full_out_dir, cat_name), chunk_size=5000,
                                  write_header=False)
                written_catalog_names.append(cat_name)
                del cat
                del bulge_db

                if has_status_file:
                    with open(status_file, 'a') as out_file:
                        duration = (time.time()-self.t_start)/3600.0
                        out_file.write('%d wrote bulge catalog after %.3e hrs\n' %
                                       (obsHistID, duration))

            if self.do_obj_type['disks']:
                disk_db = diskDESCQAObject(self.descqa_catalog)
                disk_db.field_ra = self.protoDC2_ra
                disk_db.field_dec = self.protoDC2_dec
                cat = self.instcats.DESCQACat(disk_db, obs_metadata=obs_md,
                                              cannot_be_null=['hasDisk', 'magNorm'])
                cat_name = 'disk_'+gal_name
                cat.sed_lookup_dir = self.sed_lookup_dir
                cat.lsstBandpassDict = self.bp_dict
                cat.photParams = self.phot_params
                cat.write_catalog(os.path.join(full_out_dir, cat_name), chunk_size=5000,
                                  write_header=False)
                written_catalog_names.append(cat_name)
                del cat
                del disk_db

                if has_status_file:
                    with open(status_file, 'a') as out_file:
                        duration = (time.time()-self.t_start)/3600.0
                        out_file.write('%d wrote disk catalog after %.3e hrs\n' %
                                       (obsHistID, duration))
            if self.do_obj_type['agn']:
                agn_db = agnDESCQAObject(self.descqa_catalog)
                agn_db._do_prefiltering = True
                agn_db.field_ra = self.protoDC2_ra
                agn_db.field_dec = self.protoDC2_dec
                agn_db.agn_params_db = self.agn_db_name
                cat = self.instcats.DESCQACat_Agn(agn_db, obs_metadata=obs_md)
                cat._agn_threads = self._agn_threads
                cat.lsstBandpassDict = self.bp_dict
                cat.photParams = self.phot_params
                cat_name = 'agn_'+gal_name
                cat.write_catalog(os.path.join(full_out_dir, cat_name), chunk_size=5000,
                                  write_header=False)
                written_catalog_names.append(cat_name)
                del cat
                del agn_db

                if has_status_file:
                    with open(status_file, 'a') as out_file:
                        duration = (time.time()-self.t_start)/3600.0
                        out_file.write('%d wrote agn catalog after %.3e hrs\n' %
                                       (obsHistID, duration))
        else:

            if not HAS_TWINKLES:
                raise RuntimeError("Cannot do_sprinkled; you have not imported "
                                   "the Twinkles modules in sims_GCRCatSimInterface")

            class SprinkledBulgeCat(SubCatalogMixin, self.instcats.DESCQACat_Bulge):
                subcat_prefix = 'bulge_'

                # must add catalog_type to fool InstanceCatalog registry into
                # accepting each iteration of these sprinkled classes as
                # unique classes (in the case where we are generating InstanceCatalogs
                # for multiple ObsHistIDs)
                catalog_type = 'sprinkled_bulge_%d' % obs_md.OpsimMetaData['obsHistID']

            class SprinkledDiskCat(SubCatalogMixin, self.instcats.DESCQACat_Disk):
                subcat_prefix = 'disk_'
                catalog_type = 'sprinkled_disk_%d' % obs_md.OpsimMetaData['obsHistID']

            class SprinkledAgnCat(SubCatalogMixin, self.instcats.DESCQACat_Twinkles):
                subcat_prefix = 'agn_'
                catalog_type = 'sprinkled_agn_%d' % obs_md.OpsimMetaData['obsHistID']
                _agn_threads = self._agn_threads

            if self.do_obj_type['sprinkled']:
                self.compoundGalICList = [SprinkledBulgeCat,
                                          SprinkledDiskCat,
                                          SprinkledAgnCat]

                self.compoundGalDBList = [bulgeDESCQAObject,
                                          diskDESCQAObject,
                                          agnDESCQAObject]

                for db_class in self.compoundGalDBList:
                    db_class.yaml_file_name = self.descqa_catalog

                gal_cat = twinklesDESCQACompoundObject(self.compoundGalICList,
                                                       self.compoundGalDBList,
                                                       obs_metadata=obs_md,
                                                       compoundDBclass=sprinklerDESCQACompoundObject,
                                                       field_ra=self.protoDC2_ra,
                                                       field_dec=self.protoDC2_dec,
                                                       agn_params_db=self.agn_db_name)

                gal_cat.sed_lookup_dir = self.sed_lookup_dir
                gal_cat.filter_on_healpix = True
                gal_cat.use_spec_map = twinkles_spec_map
                gal_cat.sed_dir = glsn_spectra_dir
                gal_cat.photParams = self.phot_params
                gal_cat.lsstBandpassDict = self.bp_dict

                written_catalog_names.append('bulge_'+gal_name)
                written_catalog_names.append('disk_'+gal_name)
                written_catalog_names.append('agn_'+gal_name)
                gal_cat.write_catalog(os.path.join(full_out_dir, gal_name), chunk_size=5000,
                                      write_header=False)
                if has_status_file:
                    with open(status_file, 'a') as out_file:
                        duration = (time.time()-self.t_start)/3600.0
                        out_file.write('%d wrote galaxy catalogs with sprinkling after %.3e hrs\n' % (obsHistID, duration))

            if self.do_obj_type['hosts']:
                host_cat = hostImage(obs_md.pointingRA, obs_md.pointingDec, fov)
                host_cat.write_host_cat(os.path.join(self.host_image_dir, 'agn_lensed_bulges'),
                                        os.path.join(self.host_data_dir, 'cosmoDC2_v1.1.4_bulge_agn_host.csv'),
                                        os.path.join(full_out_dir, sprinkled_host_name))
                host_cat.write_host_cat(os.path.join(self.host_image_dir,'agn_lensed_disks'),
                                        os.path.join(self.host_data_dir, 'cosmoDC2_v1.1.4_disk_agn_host.csv'),
                                        os.path.join(full_out_dir, sprinkled_host_name), append=True)
                host_cat.write_host_cat(os.path.join(self.host_image_dir, 'sne_lensed_bulges'),
                                        os.path.join(self.host_data_dir, 'cosmoDC2_v1.1.4_bulge_sne_host.csv'),
                                        os.path.join(full_out_dir, sprinkled_host_name), append=True)
                host_cat.write_host_cat(os.path.join(self.host_image_dir, 'sne_lensed_disks'),
                                        os.path.join(self.host_data_dir, 'cosmoDC2_v1.1.4_disk_sne_host.csv'),
                                        os.path.join(full_out_dir, sprinkled_host_name), append=True)

                written_catalog_names.append(sprinkled_host_name)

                if has_status_file:
                    with open(status_file, 'a') as out_file:
                        duration = (time.time()-self.t_start)/3600.0
                        out_file.write('%d wrote lensing host catalog after %.3e hrs\n' % (obsHistID, duration))

        # SN instance catalogs
        if self.sn_db_name is not None and self.do_obj_type['sne']:
            phosimcatalog = snphosimcat(self.sn_db_name,
                                        obs_metadata=obs_md,
                                        objectIDtype=42,
                                        sedRootDir=full_out_dir)

            phosimcatalog.photParams = self.phot_params
            phosimcatalog.lsstBandpassDict = self.bp_dict

            snOutFile = 'sne_cat_{}.txt'.format(obsHistID)
            phosimcatalog.write_catalog(os.path.join(full_out_dir, snOutFile),
                                        chunk_size=5000, write_header=False)

            written_catalog_names.append(snOutFile)

            if has_status_file:
                with open(status_file, 'a') as out_file:
                    duration =(time.time()-self.t_start)/3600.0
                    out_file.write('%d wrote SNe catalog after %.3e hrs\n' %
                                   (obsHistID, duration))

        make_instcat_header(self.star_db, obs_md,
                            os.path.join(full_out_dir, phosim_cat_name),
                            object_catalogs=written_catalog_names)

        if os.path.exists(os.path.join(full_out_dir, gal_name)):
            full_name = os.path.join(full_out_dir, gal_name)
            with open(full_name, 'r') as in_file:
                gal_lines = in_file.readlines()
                if len(gal_lines) > 0:
                    raise RuntimeError("%d lines in\n%s\nThat file should be empty" %
                                       (len(gal_lines), full_name))
            os.unlink(full_name)

        # gzip the object files.
        gzip_process_list = []
        for orig_name in written_catalog_names:
            full_name = os.path.join(full_out_dir, orig_name)
            if not os.path.exists(full_name):
                continue
            p = subprocess.Popen(args=['gzip', '-f', full_name])
            gzip_process_list.append(p)

            if len(gzip_process_list) >= self.gzip_threads:
                for p in gzip_process_list:
                    p.wait()
                gzip_process_list = []

        for p in gzip_process_list:
            p.wait()

        if not skip_tarball:
            if has_status_file:
                with open(status_file, 'a') as out_file:
                    out_file.write("%d tarring\n" % obsHistID)
                    p = subprocess.Popen(args=['tar', '-C', out_dir,
                                               '-cf', tar_name,
                                               '%.8d' % obsHistID])
            p.wait()
            p = subprocess.Popen(args=['rm', '-rf', full_out_dir])
            p.wait()

        if has_status_file:
            with open(status_file, 'a') as out_file:
                duration = (time.time()-self.t_start)/3600.0
                out_file.write('%d all done -- took %.3e hrs\n' %
                               (obsHistID, duration))

        print("all done with %d" % obsHistID)
        if has_status_file:
            return status_file
        return None

def make_instcat_header(star_db, obs_md, outfile, object_catalogs=(),
                        nsnap=1, vistime=30., minsource=100):
    """
    Write the header part of an instance catalog.

    Parameters
    ----------
    star_db: lsst.sims.catUtils.baseCatalogModels.StarObj
        InstanceCatalog object for stars.  Connects to the UW fatboy db server.
    obs_md: lsst.sims.utils.ObservationMetaData
        Observation metadata object.
    object_catalogs: sequence [()]
        Object catalog names to include in base phosim instance catalog.
        Defaults to an empty tuple.
    nsnap: int [1]
        Number of snaps per visit.
    vistime: float [30.]
        Visit time in seconds.
    minsource: int [100]
        Minimum number of objects for phosim.py to simulate a chip.
        Ignored.  As of 26 Nov. 2019 this value is no longer written to the
        header file.

    Returns
    -------
    lsst.sims.catUtils.exampleCatalogDefinitions.PhoSimCatalogPoint object
    """
    cat = PhoSimCatalogPoint(star_db, obs_metadata=obs_md)
    cat.phoSimHeaderMap = copy.deepcopy(DefaultPhoSimHeaderMap)
    cat.phoSimHeaderMap['nsnap'] = nsnap
    cat.phoSimHeaderMap['vistime'] = vistime

    with open(outfile, 'w') as output:
        cat.write_header(output)
        for cat_name in object_catalogs:
            output.write('includeobj %s.gz\n' % cat_name)
    return cat


def get_obs_md(obs_gen, obsHistID, fov=2, dither=True):
    """
    Get the ObservationMetaData object for the specified obsHistID.

    Parameters
    ----------
    obs_gen: lsst.sims.catUtils.utils.ObservationMetaDataGenerator
        Object that reads the opsim db file and generates obs_md objects.
    obsHistID: int
        The ID number of the desired visit.
    fov: float [2]
        Field-of-view angular radius in degrees.  2 degrees will cover
        the LSST focal plane.
    dither: bool [True]
        Flag to apply dithering in the opsim db file.

    Returns
    -------
    lsst.sims.utils.ObservationMetaData object
    """
    obs_md_list = obs_gen.getObservationMetaData(obsHistID=obsHistID,
                                                 boundType='circle',
                                                 boundLength=fov,
                                                 limit=1)

    if len(obs_md_list) == 0:
        print("There is no obsHistID == %d" % obsHistID)
        return None

    obs_md = obs_md_list[0]

    if dither:
        obs_md.pointingRA \
            = np.degrees(obs_md.OpsimMetaData['descDitheredRA'])
        obs_md.pointingDec \
            = np.degrees(obs_md.OpsimMetaData['descDitheredDec'])
        obs_md.OpsimMetaData['rotTelPos'] \
            = obs_md.OpsimMetaData['descDitheredRotTelPos']
        obs_md.rotSkyPos \
            = np.degrees(_getRotSkyPos(obs_md._pointingRA, obs_md._pointingDec,
                                       obs_md, obs_md.OpsimMetaData['rotTelPos']))
    return obs_md


def get_instance_catalogs():


    InstCats = namedtuple('InstCats', ['StarInstCat', 'BrightStarInstCat',
                                       'DESCQACat', 'DESCQACat_Bulge',
                                       'DESCQACat_Disk', 'DESCQACat_Agn',
                                       'DESCQACat_Twinkles'])

    if HAS_TWINKLES:
        return InstCats(MaskedPhoSimCatalogPoint, BrightStarCatalog,
                        PhoSimDESCQA, DESCQACat_Bulge, DESCQACat_Disk,
                        PhoSimDESCQA_AGN, DESCQACat_Twinkles)
    else:
        return InstCats(MaskedPhoSimCatalogPoint, BrightStarCatalog,
                        PhoSimDESCQA, DESCQACat_Bulge, DESCQACat_Disk,
                        PhoSimDESCQA_AGN, None)


class DESCQACat_Bulge(PhoSimDESCQA):

    cannot_be_null=['hasBulge', 'magNorm']

class DESCQACat_Disk(PhoSimDESCQA):

    cannot_be_null=['hasDisk', 'magNorm']


class MaskedPhoSimCatalogPoint(VariabilityStars, PhoSimCatalogPoint):
    disable_proper_motion = False
    min_mag = None
    column_outputs = ['prefix', 'uniqueId', 'raPhoSim', 'decPhoSim',
                      'maskedMagNorm', 'sedFilepath',
                      'redshift', 'gamma1', 'gamma2', 'kappa',
                      'raOffset', 'decOffset',
                      'spatialmodel', 'internalExtinctionModel',
                      'galacticExtinctionModel', 'galacticAv', 'galacticRv']
    protoDc2_half_size = 2.5*np.pi/180.

    @compound('quiescent_lsst_u', 'quiescent_lsst_g',
              'quiescent_lsst_r', 'quiescent_lsst_i',
              'quiescent_lsst_z', 'quiescent_lsst_y')
    def get_quiescentLSSTmags(self):
        return np.array([self.column_by_name('umag'),
                         self.column_by_name('gmag'),
                         self.column_by_name('rmag'),
                         self.column_by_name('imag'),
                         self.column_by_name('zmag'),
                         self.column_by_name('ymag')])

    @cached
    def get_maskedMagNorm(self):

        # What follows is a terrible hack.
        # There's a bug in CatSim such that, it won't know
        # to query for quiescent_lsst_* until after
        # the database query has been run.  Fixing that
        # bug is going to take more careful thought than
        # we have time for before Run 1.1, so I am just
        # going to request those columns now to make sure
        # they get queried for.
        self.column_by_name('quiescent_lsst_u')
        self.column_by_name('quiescent_lsst_g')
        self.column_by_name('quiescent_lsst_r')
        self.column_by_name('quiescent_lsst_i')
        self.column_by_name('quiescent_lsst_z')
        self.column_by_name('quiescent_lsst_y')

        raw_norm = self.column_by_name('phoSimMagNorm')
        if self.min_mag is None:
            return raw_norm
        return np.where(raw_norm < self.min_mag, self.min_mag, raw_norm)

    @cached
    def get_inProtoDc2(self):
        ra_values = self.column_by_name('raPhoSim')
        ra = np.where(ra_values < np.pi, ra_values, ra_values - 2.*np.pi)
        dec = self.column_by_name('decPhoSim')
        return np.where((ra > -self.protoDc2_half_size) &
                        (ra < self.protoDc2_half_size) &
                        (dec > -self.protoDc2_half_size) &
                        (dec < self.protoDc2_half_size), 1, None)

    def column_by_name(self, colname):
        if (self.disable_proper_motion and
            colname in ('properMotionRa', 'properMotionDec',
                        'radialVelocity', 'parallax')):
            return np.zeros(len(self.column_by_name('raJ2000')), dtype=np.float)
        return super(MaskedPhoSimCatalogPoint, self).column_by_name(colname)


class BrightStarCatalog(PhoSimCatalogPoint):
    min_mag = None

    @cached
    def get_isBright(self):
        raw_norm = self.column_by_name('phoSimMagNorm')
        return np.where(raw_norm < self.min_mag, raw_norm, np.NaN)
