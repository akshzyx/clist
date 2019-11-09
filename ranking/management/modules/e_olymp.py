#!/usr/bin/env python

import re
from pprint import pprint
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor as PoolExecutor

import tqdm

from common import REQ, BaseModule, parsed_table


class Statistic(BaseModule):

    def __init__(self, **kwargs):
        super(Statistic, self).__init__(**kwargs)
        if not self.standings_url:
            self.standings_url = f'{self.url.rstrip("/")}/leaderboard'

    def get_standings(self, users=None):

        result = {}
        problems_info = OrderedDict()

        page = REQ.get(self.standings_url)
        match = re.findall('<a[^>]href="[^"]*page=[0-9]+"[^>]*>(?P<n_page>[0-9]+)</a>', page)
        n_page = 1 if not match else int(match[-1])

        def fetch_page(page_index):
            url = self.standings_url
            if page_index:
                url += f'?page={page_index}'
            return REQ.get(url), url

        place = 0
        prev = None
        with PoolExecutor(max_workers=8) as executor, tqdm.tqdm(total=n_page, desc='fetch pages') as pbar:
            for page, url in executor.map(fetch_page, range(n_page)):
                pbar.set_postfix(url=url)
                pbar.update(1)

                regex = '<table[^>]*>.*?</table>'
                match = re.search(regex, page, re.DOTALL)
                html_table = match.group(0)
                table = parsed_table.ParsedTable(html_table)
                for r in table:
                    row = {}
                    problems = row.setdefault('problems', {})
                    for k, v in list(r.items()):
                        k = k.split()[0]
                        if k.lower() == 'score':
                            solving, *a = v.value.split()
                            row['solving'] = int(solving)
                            if a:
                                row['penalty'] = int(re.sub(r'[\(\)]', '', a[0]))
                        elif len(k) == 1:
                            problems_info[k] = {'short': k}
                            if 'title' in v.attrs:
                                problems_info[k]['name'] = v.attrs['title']

                            if '-' in v.value or '+' in v.value:
                                p = problems.setdefault(k, {})
                                if ' ' in v.value:
                                    point, time = v.value.split()
                                    p['time'] = time
                                else:
                                    point = v.value
                                if point == '+0':
                                    point = '+'
                                p['result'] = point
                            elif v.value.isdigit():
                                p = problems.setdefault(k, {})
                                p['result'] = v.value
                        elif k.lower() == 'user':
                            row['member'] = v.value
                        else:
                            row[k] = v.value

                    if 'penalty' not in row:
                        solved = [p for p in list(problems.values()) if p['result'] == '100']
                        row['solved'] = {'solving': len(solved)}

                    curr = (row['solving'], row.get('penalty'))
                    if prev is None or prev != curr:
                        place += 1
                        prev = curr
                    row['place'] = place

                    result[row['member']] = row

        standings = {
            'result': result,
            'url': self.standings_url,
            'problems': list(problems_info.values()),
        }
        return standings


if __name__ == "__main__":
    statictic = Statistic(url='https://www.e-olymp.com/en/contests/13532', standings_url=None)
    pprint(statictic.get_result('chportko'))
    statictic = Statistic(url='https://www.e-olymp.com/en/contests/13745', standings_url=None)
    pprint(statictic.get_result('Ivan_Z'))