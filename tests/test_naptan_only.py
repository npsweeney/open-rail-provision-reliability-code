"""Guard direct matching against ambiguous names, bad aliases and point duplication."""
import tempfile, unittest
from pathlib import Path
import pandas as pd
from src.data.stations import attach_naptan_locations
class NaptanOnlyTests(unittest.TestCase):
 def test_alias_resolves_ambiguous_name_and_retains_unmatched(self):
  with tempfile.TemporaryDirectory() as tmp:
   n,m=Path(tmp)/'n.csv',Path(tmp)/'m.csv'
   pd.DataFrame({'ATCOCode':['9100NORTH','9100SOUTH'],'CommonName':['Central Rail Station']*2,'StopType':['RLY']*2,'Status':['active']*2,'GridType':['UKOS']*2,'Easting':[400000,500000],'Northing':[300000,400000]}).to_csv(n,index=False)
   stations=pd.DataFrame({'station_name':['Central','Missing']})
   out=attach_naptan_locations(stations,n).set_index('station_name')
   self.assertEqual(out.loc['Central','location_status'],'ambiguous_name')
   self.assertEqual(out.loc['Missing','location_status'],'unmatched')
   pd.DataFrame({'station_name':['Central'],'naptan_atco_code':['9100NORTH']}).to_csv(m,index=False)
   out=attach_naptan_locations(stations,n,m).set_index('station_name')
   self.assertEqual(out.loc['Central','easting'],400000)
   self.assertEqual(out.loc['Central','location_status'],'official_station_reference')
   pd.DataFrame({'station_name':['Central'],'naptan_atco_code':['9100ABSENT']}).to_csv(m,index=False)
   with self.assertRaises(ValueError):attach_naptan_locations(stations,n,m)
 def test_multiple_orr_names_cannot_silently_share_a_point(self):
  with tempfile.TemporaryDirectory() as tmp:
   n,m=Path(tmp)/'n.csv',Path(tmp)/'m.csv'
   pd.DataFrame({'ATCOCode':['9100POINT'],'CommonName':['Central Rail Station'],'StopType':['RLY'],'Status':['active'],'GridType':['UKOS'],'Easting':[400000],'Northing':[300000]}).to_csv(n,index=False)
   pd.DataFrame({'station_name':['Alias'],'naptan_atco_code':['9100POINT']}).to_csv(m,index=False)
   with self.assertRaises(ValueError):attach_naptan_locations(pd.DataFrame({'station_name':['Central','Alias']}),n,m)
