"""Prevent split-LAD duplication and inconsistent ITL population totals."""
import json
import tempfile
import unittest
from pathlib import Path
import pandas as pd
from shapely.geometry import box
from src.data.itl import assign_itls, itl_population, read_boundaries, station_geographies


class ItlGeographiesTests(unittest.TestCase):
    def lookup(self):
        return pd.DataFrame([
            ('S12000021','TLM20','Highlands','TLM2','Highlands','TLM','Scotland'),
            ('S12000021','TLM93','Ayrshire','TLM9','Southern','TLM','Scotland')],
            columns=['LAD24CD','ITL325CD','ITL325NM','ITL225CD','ITL225NM','ITL125CD','ITL125NM'])

    def test_gb_boundary_catalogue_retains_wales_and_excludes_northern_ireland(self):
        with tempfile.TemporaryDirectory() as directory:
            features=[{'type':'Feature','properties':{'ITL125CD':code,'ITL125NM':name},
                'geometry':{'type':'Polygon','coordinates':[[[-4,52],[-3,52],[-3,53],[-4,53],[-4,52]]]}}
                for code,name in [('TLL','Wales'),('TLN','Northern Ireland')]]
            Path(directory,'itl1_2025_bfe.geojson').write_text(json.dumps({'type':'FeatureCollection','features':features}))
            catalogue,_=read_boundaries(Path(directory),'itl1')
            self.assertEqual(catalogue.area_code.tolist(),['TLL'])

    def test_split_lad_assigns_mainland_and_island_once_each(self):
        stations=pd.DataFrame({'station_name':['Mainland','Island'],'area_code':['S12000021']*2,
            'location_status':['official_station_reference']*2,'easting':[15,5],'northing':[5,5]})
        catalogue=pd.DataFrame({'area_code':['TLM20','TLM93']})
        output=assign_itls(stations,self.lookup(),catalogue,{'TLM20':box(0,0,10,10),'TLM93':box(11,0,20,10)})
        self.assertEqual(len(output),2)
        self.assertTrue(output.station_name.is_unique)
        self.assertEqual(output.set_index('station_name').loc['Mainland','itl2_code'],'TLM9')
        self.assertEqual(output.set_index('station_name').loc['Island','itl2_code'],'TLM2')

    def test_split_lad_ambiguous_border_and_distant_point_fail(self):
        station=pd.DataFrame({'station_name':['A'],'area_code':['S12000021'],
            'location_status':['official_station_reference'],'easting':[10],'northing':[5]})
        catalogue=pd.DataFrame({'area_code':['TLM20','TLM93']})
        with self.assertRaisesRegex(ValueError,'multiple'):
            assign_itls(station,self.lookup(),catalogue,{'TLM20':box(0,0,10,10),'TLM93':box(10,0,20,10)})
        station['easting']=1000
        with self.assertRaisesRegex(ValueError,'needs review'):
            assign_itls(station,self.lookup(),catalogue,{'TLM20':box(0,0,10,10),'TLM93':box(11,0,20,10)})

    def test_single_station_link_preserves_independent_ttwa_and_checks_lad(self):
        stations=pd.DataFrame({'station_name':['A'],'naptan_atco_code':['9100A'],'tiploc':['A'],
            'longitude':[-4.6],'latitude':[55.6],'easting':[230000],'northing':[640000],
            'coordinate_source':['NaPTAN'],'mapping_method':['exact_name'],
            'area_code':['S12000021'],'area_name':['North Ayrshire'],'area_assignment':['inside']})
        assigned=pd.DataFrame({'station_name':['A'],'lad_code':['S12000021'],
            'itl3_code':['TLM93'],'itl3_name':['North Ayrshire and East Ayrshire'],
            'itl2_code':['TLM9'],'itl2_name':['Southern Scotland'],'itl1_code':['TLM'],'itl1_name':['Scotland']})
        ttwa=pd.DataFrame({'station_name':['A'],'area_code':['S22000001'],'area_name':['Labour market']})
        linked=station_geographies(stations,assigned,ttwa)
        self.assertEqual(len(linked),1)
        self.assertEqual(linked.lad_code.iloc[0],'S12000021')
        self.assertEqual(linked.ttwa_code.iloc[0],'S22000001')
        self.assertTrue(station_geographies(stations,assigned).ttwa_code.isna().all())
        with self.assertRaisesRegex(ValueError,'same stations'):
            station_geographies(stations,assigned,ttwa.iloc[:0])
        assigned['lad_code']='S12000000'
        with self.assertRaisesRegex(ValueError,'paths disagree'):
            station_geographies(stations,assigned,ttwa)

    def test_published_scottish_parts_do_not_duplicate_whole_lad_population(self):
        lads=pd.DataFrame({'area_code':['S12000021'],'population_2024':[100]})
        scotland=pd.DataFrame({'area_code':['TLM20','TLM93'],'population_2024':[10,90]})
        catalogue=pd.DataFrame({'area_code':['TLM20','TLM93'],'area_name':['Highlands','Ayrshire']})
        result=itl_population(self.lookup(),lads,scotland,catalogue,'itl3')
        self.assertEqual(result.population_2024.sum(),100)
        scotland.loc[0,'population_2024']=11
        with self.assertRaisesRegex(ValueError,'vintages disagree'):
            itl_population(self.lookup(),lads,scotland,catalogue,'itl3')

if __name__=='__main__':unittest.main()
