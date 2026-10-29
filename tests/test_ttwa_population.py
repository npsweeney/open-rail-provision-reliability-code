"""Controls for complete OA joins, cross-border populations and per-head units."""
import unittest
from pathlib import Path
import pandas as pd
from src.data.population import aggregate_population
from src.data.ttwa import add_population


class TtwaPopulationTests(unittest.TestCase):
    def inputs(self):
        lookup=pd.DataFrame({'OA21CD':['OA1','OA2'],'TTWA11CD':['K1','E1']})
        population=pd.DataFrame({'GEOGRAPHY_CODE':['OA1','OA2'],'OBS_VALUE':[30,70]})
        scotland=pd.DataFrame({'Region':['Berwick','EDINBURGH'],'population_scotland_2024':[5,20]})
        areas=pd.DataFrame({'area_code':['K1','E1','S1'],'area_name':['Berwick','Example','Edinburgh']})
        return lookup,population,scotland,areas

    def test_cross_border_parts_add_without_double_counting(self):
        out,joined,scotland=aggregate_population(*self.inputs())
        out=out.set_index('area_code')
        self.assertEqual(out.loc['K1','population_2024'],35)
        self.assertEqual(out.loc['S1','population_2024'],20)
        self.assertEqual(out.population_2024.sum(),125)
        self.assertTrue(joined.population_join.eq('both').all())
        self.assertTrue(scotland.scottish_join.eq('both').all())

    def test_duplicate_output_area_is_rejected(self):
        lookup,pop,scot,areas=self.inputs()
        with self.assertRaisesRegex(ValueError,'unique'):
            aggregate_population(pd.concat([lookup,lookup.iloc[[0]]]),pop,scot,areas)
        with self.assertRaisesRegex(ValueError,'unique'):
            aggregate_population(lookup,pd.concat([pop,pop.iloc[[0]]]),scot,areas)

    def test_missing_output_area_is_rejected(self):
        lookup,pop,scot,areas=self.inputs()
        with self.assertRaisesRegex(ValueError,'complete set'):
            aggregate_population(lookup,pop.iloc[[0]],scot,areas)

    def test_unknown_scottish_name_is_rejected(self):
        lookup,pop,scot,areas=self.inputs();scot.loc[0,'Region']='Unknown area'
        with self.assertRaisesRegex(ValueError,'Unmatched Scottish'):
            aggregate_population(lookup,pop,scot,areas)

    def test_calls_per_ten_thousand_per_calendar_day(self):
        summary=pd.DataFrame({'area_code':['X'],'area_name':['Example'],'scheduled_stops':[36500.],'period_days':[365]})
        pop=pd.DataFrame({'area_code':['X'],'area_name':['Example'],'population_2024':[10000.],'population_year':[2024]})
        out=add_population(summary,pop)
        self.assertEqual(out.scheduled_calls_per_10000_residents_per_day.iloc[0],100)
        self.assertEqual(len(out),1)

    def test_population_year_and_completeness_are_required(self):
        summary=pd.DataFrame({'area_code':['X'],'area_name':['Example'],'scheduled_stops':[100.],'period_days':[10]})
        pop=pd.DataFrame({'area_code':['X'],'area_name':['Example'],'population_2024':[100.],'population_year':[2024]})
        for field,value in [('population_year',2023),('population_2024',0),('area_code','OTHER')]:
            changed=pop.copy();changed[field]=value
            with self.assertRaises(ValueError):add_population(summary,changed)
