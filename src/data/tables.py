"""Prepare figure and table CSVs from completed ORR calculations; R renders the presentation."""
import numpy as np
import pandas as pd
from .areas import aggregate_area_period


# Prepare the CSVs used by the figure and table renderer
def build(source, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    rows = pd.read_csv(source / 'station_period.csv')
    pooled = pd.read_csv(source / 'station_window.csv')
    locations = rows[['station_name']].drop_duplicates().assign(area_code='GB', area_name='Great Britain')
    national = aggregate_area_period(rows, locations).sort_values('period_start')
    national.to_csv(outdir / 'national_period.csv', index=False)
    population = pd.read_csv(source / 'itl1_orr_provision.csv')
    population = population.loc[population.period_end.eq(population.period_end.max())]
    assert population.population_2024.notna().all()
    cancelled_denom = pooled.cancellation_observed_scheduled_stops.sum()
    delay_denom = pooled.punctuality_observed_recorded_stops.sum()
    summary = pd.DataFrame([
        ('Reporting periods / calendar days', f'{len(national)} / {pooled.period_days.iloc[0]:,}', '---'),
        ('Stations', f'{len(pooled):,}', '---'),
        ('Scheduled station calls', f'{pooled.scheduled_stops.sum():,.0f}', f'{100*(rows.scheduled_stops.notna() & ~rows.station_period_not_applicable).sum()/(~rows.station_period_not_applicable).sum():.2f}'),
        ('Calls per 10,000 residents per day', f'{10000*pooled.scheduled_stops.sum()/pooled.period_days.iloc[0]/population.population_2024.sum():.2f}', '---'),
        ('Cancelled (\\%)', f'{100*pooled.estimated_cancelled_calls.sum()/cancelled_denom:.2f}', f'{100*cancelled_denom/pooled.scheduled_stops.sum():.2f}'),
        ('Delayed ($\\geq$ 3 mins; \\%)', f'{100*(1-pooled.estimated_time_to_3_calls.sum()/delay_denom):.2f}', f'{100*delay_denom/pooled.recorded_stops.sum():.2f}')], columns=['Measure', 'Value', 'Coverage'])
    summary.to_csv(outdir / 'headline_summary.csv', index=False)
    for level,filename in [('itl1','itl1_orr_provision_window.csv'),('lad','lad_orr_provision_window.csv')]:
        data=pd.read_csv(source/filename);data=data.loc[data.period_end.eq(data.period_end.max())].copy()
        data['area_name']=data.area_name.replace({'Yorkshire and The Humber':'Yorkshire & Humber'})
        if level=='lad': data=data.loc[data.provision_status.eq('complete')].nlargest(10,'scheduled_calls_per_10000_residents')
        else: data=data.sort_values('scheduled_calls_per_10000_residents',ascending=False,na_position='last')
        data.to_csv(outdir/(level+'_display_rows.csv'),index=False)
    station_lads = pd.read_csv(source / 'station_lad_assignments.csv')[['station_name', 'area_name']].rename(columns={'area_name':'LAD'})
    assert station_lads.station_name.is_unique
    extremes=[]
    for measure,volume,rate in [('faci','scheduled_stops','cancellations_pct'),('fadi','recorded_stops','delay_pct')]:
        column = 'exploratory_' + measure
        ordered = pooled.loc[pooled[column].notna()].sort_values(
            [column, 'station_name'], ascending=[False, True]).copy()
        ordered['Rank'] = np.arange(1, len(ordered) + 1)
        chosen = pd.concat([ordered.head(5), ordered.tail(5)]).copy()
        chosen['Index']=measure.upper();chosen['Calls/day']=chosen['exploratory_'+volume]/chosen[measure + '_eligible_days']
        chosen['Periods']=chosen[measure + '_eligible_periods'].astype(int)
        chosen['Rate']=chosen['exploratory_'+rate];chosen['Score']=chosen[column]
        chosen = chosen.merge(station_lads, on='station_name', how='left', validate='one_to_one')
        assert chosen.LAD.notna().all() and len(chosen) == 10
        extremes.append(chosen[['Rank','Index','station_name','LAD','Periods','Calls/day','Rate','Score']])
    extremes=pd.concat(extremes)
    extremes.to_csv(outdir/'index_extremes.csv',index=False)
    assert pooled.station_name.is_unique and len(pooled) == rows.station_name.nunique()
    assert len(national) == rows.time_period.nunique()
    print('Figure and table CSVs:',outdir)
