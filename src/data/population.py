"""Build 2024 populations for GB TTWAs from ONS Output Areas and NRS Scottish parts."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import urllib.parse
import pandas as pd
from .ttwa import read_ttwas
from .boundaries import fetch

LOOKUP_URL = 'https://hub.arcgis.com/api/v3/datasets/03f3617099564e33a9e4390051f10e4a_0/downloads/data?format=csv&spatialRefId=4326&where=1%3D1'
SCOTLAND_URL = 'https://www.gov.scot/binaries/content/documents/govscot/publications/statistics/2022/03/sub-scotland-economic-statistics-database/documents/sub-scotland-economic-statistics-database/sub-scotland-economic-statistics-database/govscot%3Adocument/Sub-Scotland%2BEconomic%2BStatistics%2BDatabase.xlsx'


# Combine English/Welsh Output Areas and Scottish TTWA components
def aggregate_population(lookup, population, scottish_parts, areas):
    # Every 2021 Output Area contributes once to its official best-fit TTWA
    if lookup.OA21CD.isna().any() or lookup.OA21CD.duplicated().any() or population.GEOGRAPHY_CODE.duplicated().any():
        raise ValueError('Output Area population and lookup keys must be unique.')
    joined = lookup[['OA21CD','TTWA11CD']].merge(population, left_on='OA21CD',right_on='GEOGRAPHY_CODE',
                                              how='outer',validate='one_to_one',indicator='population_join')
    if joined.population_join.ne('both').any() or joined.OBS_VALUE.isna().any() or joined.OBS_VALUE.lt(0).any():
        raise ValueError('Output Area populations and TTWA lookup do not cover the same complete set.')
    ew = joined.groupby('TTWA11CD').OBS_VALUE.sum().rename('population_ew_2024')
    # Scottish totals include the Scottish portions of Berwick and Carlisle
    # Match the published TTWA names exactly after punctuation/case normalisation
    catalogue = areas[['area_code','area_name']].copy()
    catalogue['name_key'] = catalogue.area_name.str.casefold().str.replace(r'[^a-z0-9]','',regex=True)
    parts = scottish_parts.copy()
    parts['name_key'] = parts.Region.str.casefold().str.replace(r'[^a-z0-9]','',regex=True)
    if catalogue.name_key.duplicated().any() or parts.name_key.duplicated().any():
        raise ValueError('Scottish TTWA names must have unique normalised keys.')
    parts = parts.merge(catalogue,on='name_key',how='left',validate='one_to_one',indicator='scottish_join')
    if parts.scottish_join.ne('both').any():
        raise ValueError('Unmatched Scottish TTWA names: ' + ', '.join(parts.loc[parts.scottish_join.ne('both'),'Region']))
    if parts.population_scotland_2024.isna().any() or parts.population_scotland_2024.le(0).any():
        raise ValueError('Scottish TTWA populations must be observed and positive.')
    result = areas[['area_code','area_name']].copy()
    result['population_ew_2024'] = result.area_code.map(ew).fillna(0)
    result['population_scotland_2024'] = result.area_code.map(parts.set_index('area_code').population_scotland_2024).fillna(0)
    result['population_2024'] = result.population_ew_2024 + result.population_scotland_2024
    if result.population_2024.le(0).any() or not set(ew.index).issubset(set(result.area_code)):
        raise ValueError('TTWA population coverage is incomplete.')
    result['population_year'] = 2024
    return result, joined, parts


# Build mid-2024 TTWA populations from official small-area estimates
def build(root):
    raw=root/'data/raw'
    cache=raw/'ttwa_population_2024';cache.mkdir(exist_ok=True)
    out=root/'data/ttwa';out.mkdir(exist_ok=True,parents=True)
    sources=[]
    lookup_path=fetch(LOOKUP_URL,raw/'ttwa_oa21_lookup.csv')
    scotland_path=fetch(SCOTLAND_URL,raw/'scotland_economic_statistics_2026.xlsx')
    sources += [{'url':LOOKUP_URL,'path':str(lookup_path)},{'url':SCOTLAND_URL,'path':str(scotland_path)}]
    lookup=pd.read_csv(lookup_path,dtype=str)
    pages=[]
    batches = (len(lookup) + 24999) // 25000
    print(f'Population: {len(lookup):,} Output Areas in {batches} batches; Nomis allows up to 25,000 values per request', flush=True)
    # Cache each required batch so interrupted downloads can resume
    for batch, offset in enumerate(range(0,len(lookup),25000), start=1):
        print(f'Population batch {batch}/{batches}: Output Areas {offset + 1:,} to {min(offset + 25000, len(lookup)):,}', flush=True)
        params={'geography':'TYPE150','date':'2024','gender':0,'c_age':200,'measures':20100,
                'select':'date_name,geography_code,obs_value,obs_status','recordlimit':25000,'recordoffset':offset}
        url='https://www.nomisweb.co.uk/api/v01/dataset/NM_2014_1.data.csv?'+urllib.parse.urlencode(params)
        path=fetch(url,cache/f'oa_population_{offset:06d}.csv')
        page=pd.read_csv(path)
        if not {'DATE_NAME','GEOGRAPHY_CODE','OBS_VALUE','OBS_STATUS'}.issubset(page):
            raise ValueError('Unexpected Nomis response: ' + str(path))
        pages.append(page);sources.append({'url':url,'path':str(path)})
        print(f'Population batch {batch}/{batches} complete: {len(page):,} Output Areas', flush=True)
    population=pd.concat(pages,ignore_index=True)
    if not population.DATE_NAME.eq(2024).all() or not population.OBS_STATUS.eq('A').all():
        raise ValueError('Population response contains another year or unavailable observations.')
    sheet=pd.read_excel(scotland_path,sheet_name='Table 18',header=16)
    year=next(c for c in sheet if str(c).strip()=='2024')
    parts=sheet.loc[sheet.Geography.eq('Travel to work areas'),['Region',year]].rename(columns={year:'population_scotland_2024'})
    catalogue,_=read_ttwas(raw/'ttwa_2011_uk_bsc_v3.zip')
    catalogue.to_csv(out/'boundary_catalogue.csv',index=False)
    areas=catalogue.loc[~catalogue.area_code.str.startswith('N')]
    result,joined,scottish=aggregate_population(lookup,population,parts,areas)
    # Reconcile both countries' components and the total to the same 2024 ONS workbook
    official=pd.read_excel(raw/'ons_mye2024_uk.xlsx',sheet_name='MYE2 - Persons',header=7).set_index('Code')['All ages']
    checks=[('England and Wales population',result.population_ew_2024.sum(),official['E92000001']+official['W92000004']),
            ('Scotland population',result.population_scotland_2024.sum(),official['S92000003']),
            ('Great Britain population',result.population_2024.sum(),official['K03000001']),
            ('Output Area coverage',len(joined),len(lookup)),('TTWA coverage',len(result),218)]
    review=pd.DataFrame(checks,columns=['check','calculated','expected']);review['difference']=review.calculated-review.expected
    if review.difference.ne(0).any():raise ValueError('TTWA population totals disagree with the source estimates.')
    result.to_csv(out/'ttwa_population_2024.csv',index=False)
    joined.to_csv(out/'oa21_population_ttwa_join.csv',index=False)
    scottish.to_csv(out/'scottish_population_ttwa_join.csv',index=False)
    sources += [{'path':str(raw/'ons_mye2024_uk.xlsx'),'role':'Country population reconciliation'},
                {'path':str(raw/'ttwa_2011_uk_bsc_v3.zip'),'role':'TTWA code and name catalogue'}]
    (cache/'sources.json').write_text(json.dumps({'year':2024,'sources':sources},indent=2)+'\n')
    print(review.to_string(index=False));print(result.loc[result.area_name.isin(['London','Bristol','Edinburgh','Glasgow','Berwick','Carlisle'])].to_string(index=False))


# Read the project root and prepare the TTWA population CSV
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    build(parser.parse_args().root)


if __name__ == '__main__':
    main()
