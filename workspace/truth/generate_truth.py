"""
This is the script that actually generates the
truth catalog based on the database of
parameters generated in generate_truth_params.

It took about 1.5 hours to run for CosmoDC2.
"""

from StarTruthModule import write_stars_to_truth
from GalaxyTruthModule import write_galaxies_to_truth

import os
import sqlite3

if __name__ == "__main__":

    db_dir = '/astro/store/pogo3/danielsf/desc_dc2_truth'
    assert os.path.isdir(db_dir)

    param_file = os.path.join(db_dir, 'sprinkled_objects.sqlite')
    assert os.path.isfile(param_file)

    db_file = os.path.join(db_dir, 'proto_dc2_truth_star_gal.db')
    if os.path.isfile(db_file):
        os.unlink(db_file)

    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cmd = '''CREATE TABLE truth
              (healpix_2048 int, object_id int, star int,
               agn int, sprinkled int, ra float, dec float,
               redshift float, u float, g float, r float, i float,
               z float, y float)'''

        cursor.execute(cmd)
        conn.commit()

        cmd = '''CREATE TABLE column_descriptions
              (name text, description text)'''
        cursor.execute(cmd)

        values = (('healpix_2048', 'healpixel containing the object (nside=2048; nested)'),
                  ('object_id', 'an int uniquely identifying objects (can collide between stars, galaxies, and sprinkled objects)'),
                  ('star', 'an int; ==1 if a star; ==0 if not'),
                  ('agn', 'an int; ==1 if galaxy has an AGN; ==0 if not'),
                  ('sprinkled', 'an int; ==1 if object added by the sprinkler; ==0 if not'),
                  ('ra', 'in degrees'),
                  ('dec', 'in degrees'),
                  ('redshift', 'cosmological only'),
                  ('u', 'observed lsst u magnitude; no dust extinction at all'),
                  ('g', 'observed lsst g magnitude; no dust extinction at all'),
                  ('r', 'observed lsst r magnitude; no dust extinction at all'),
                  ('i', 'observed lsst i magnitude; no dust extinction at all'),
                  ('z', 'observed lsst z magnitude; no dust extinction at all'),
                  ('y', 'observed lsst y magnitude; no dust extinction at all'))

        cursor.executemany('INSERT INTO column_descriptions VALUES (?,?)',values)
        conn.commit()

    write_stars_to_truth(output=db_file,
                         n_side=2048,
                         n_procs=20,
                         clobber=False)

    print('wrote stars')

    write_galaxies_to_truth(input=param_file,
                            output=db_file,
                            n_side=2048,
                            n_procs=20,
                            clobber=False)

    print('wrote galaxies')

    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()

        cursor.execute('CREATE INDEX obj_id ON truth (object_id)')
        conn.commit()
        print('made object_id index')

        cursor.execute('CREATE INDEX is_star ON truth (star)')
        conn.commit()
        print('made is_star index')

        cursor.execute('CREATE INDEX is_sprinkled ON truth (sprinkled)')
        conn.commit()
        print('made is_sprinkled index')

        cursor.execute('CREATE INDEX healpix ON truth (healpix_2048)')
        conn.commit()
        print('made healpix index')
