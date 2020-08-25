# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Mar 12 15:37:47 2019

@author: python
"""
from collections import namedtuple, defaultdict
import json, pandas as pd
from itertools import chain
from toolz import partition_all, keyfilter
from gateway.spider.xml import ASSERT_URL_MAPPING, ASSET_SUPPLEMENT_URL
from gateway.driver.client import tsclient
from gateway.spider import Crawler
from gateway.database.asset_writer import AssetWriter
from gateway.driver.tools import _parse_url


AssetData = namedtuple(
    'AssetData', (
        'equities',
        'convertibles',
        'funds',
    ),
)

__all__ = ['AssetSpider']


class AssetSpider(Crawler):
    """
        a.获取全部的资产标的 --- equity convertible etf
        b.筛选出需要更新的标的（与缓存进行比较）
    """
    __slots__ = ['_asset_caches']

    def __init__(self, engine_path=None):
        self._asset_caches = dict()
        self._writer = AssetWriter(engine_path)
        self.missing = defaultdict(list)

    @staticmethod
    def _request_equities():
        # 获取存量股票包括退市
        raw = json.loads(_parse_url(ASSERT_URL_MAPPING['equity'], bs=False))
        equities = [item['f12'] for item in raw['data']['diff']]
        return equities

    @staticmethod
    def _request_convertibles():
        # 获取上市的可转债的标的
        page = 1
        bonds = []
        while True:
            bond_url = ASSERT_URL_MAPPING['convertible'] % page
            text = _parse_url(bond_url, encoding='utf-8', bs=False)
            text = json.loads(text)
            data = text['data']
            if data:
                bonds.append(data)
                page = page + 1
            else:
                break
        # 过滤未上市的可转债
        bond_mappings = {bond[0]['BONDCODE']: bond[0] for bond in bonds if bond[0]['LISTDATE'] != '-'}
        # bond_id : bond_basics
        return bond_mappings

    @staticmethod
    def _request_funds(symbols=None):
        # 获取存量的ETF
        # 基金主要分为 固定收益 分级杠杆（A/B） ( ETF场内| QDII-ETF )
        obj = _parse_url(ASSERT_URL_MAPPING['fund'])
        raw = [data.find_all('td') for data in obj.find_all(id='tableDiv')]
        text = [t.get_text() for t in raw[0]]
        frame = pd.DataFrame(partition_all(14, text[18:]), columns=text[2:16])
        frame['基金简称'] = frame['基金简称'].apply(lambda x: x[:-5])
        # frame = frame.apply(lambda x: x['基金简称'][:-5], axis=1)
        # frame --- slice depend on symbols
        fund_frames = frame[frame['基金简称'].isin(symbols)] if symbols else frame
        fund_frames.loc[:, 'asset_type'] = 'fund'
        return fund_frames

    @staticmethod
    def _request_duals():
        # 获取存量AH两地上市的标的
        dual_mappings = {}
        page = 1
        while True:
            url = ASSERT_URL_MAPPING['dual'] % page
            raw = _parse_url(url, bs=False, encoding=None)
            raw = json.loads(raw)
            diff = raw['data']
            if diff and len(diff['diff']):
                # f12 -- hk ; 191 -- code
                diff = {item['f191']: item['f12'] for item in diff['diff']}
                dual_mappings.update(diff)
                page = page + 1
            else:
                break
        return dual_mappings

    def _update_assets(self):
        # 将新上市的标的 --- equity etf convertible --- request_cache
        request_cache = {}
        # update equity
        equities = self._request_equities()
        request_cache['equity'] = set(equities) - set(self._asset_caches.get('equity', []))
        self._asset_caches['equity'] = equities
        print('equities successfully', equities)
        # update convertible
        convertibles = self._request_convertibles()
        request_cache['convertible'] = set(convertibles) - set(self._asset_caches.get('convertible', {}))
        self._asset_caches['convertible'] = convertibles
        print('convertibles successfully', convertibles)
        # update funds
        funds = self._request_funds()
        request_cache['fund'] = set(funds['基金代码'].values) - set(self._asset_caches.get('fund', []))
        self._asset_caches['fund'] = funds
        print('funds successfully', funds)
        # update duals
        duals = self._request_duals()
        update_duals = set(duals) - set(self._asset_caches.get('dual', {}))
        request_cache['dual'] = keyfilter(lambda x: x in update_duals, duals)
        self._asset_caches['dual'] = duals
        print('duals successfully', duals)

        return request_cache

    @staticmethod
    def _request_equity_basics(code):
        url = ASSET_SUPPLEMENT_URL['equity_supplement'] % code
        obj = _parse_url(url)
        table = obj.find('table', {'id': 'comInfo1'})
        tag = [item.findAll('td') for item in table.findAll('tr')]
        tag_chain = list(chain(*tag))
        raw = [item.get_text() for item in tag_chain]
        # 去除格式
        raw = [i.replace('：', '') for i in raw]
        raw = [i.strip() for i in raw]
        brief = list(zip(raw[::2], raw[1::2]))
        mapping = {item[0]: item[1] for item in brief}
        mapping.update({'代码': code})
        return mapping

    def _request_equities_basics(self, update_cache):
        # 获取dual
        dual_equity = update_cache['dual']
        equities = update_cache['equity']
        status = tsclient.to_ts_stats()
        basics = []
        # 公司基本情况
        for code in equities:
            try:
                mapping = self._request_equity_basics(code)
                if code in dual_equity:
                    dual = dual_equity[code]
                    mapping.update({'港股': dual})
                    basics.append(mapping)
                    if code in self.missing['equity']:
                        self.missing['equity'].remove(code)
                print('scrapy code % s from sina successfully' % code)
            except Exception as e:
                print('code:%s due to %s' % (code, e))
                self.missing['equity'].append(code)
        # transform [dict] to DataFrame
        frame = pd.DataFrame(basics)
        frame.set_index('代码', inplace=True)
        # append status
        frame = frame.append(status)
        frame.fillna('null', inplace=True)
        # append asset_type
        frame.loc[:, 'asset_type'] = 'equity'
        return frame

    @staticmethod
    def _request_convertible_basics(update_cache):
        # 剔除未上市的
        bond_mappings = update_cache['convertible']
        # bond basics 已上市的basics
        text = _parse_url(ASSET_SUPPLEMENT_URL['convertible_supplement'], encoding=None, bs=False)
        text = json.loads(text)
        # combine two dict object --- single --- 保持数据的完整性
        basics = [basic['cell'].update(bond_mappings[basic['id']]) for basic in text['rows']]
        basics_frame = pd.DataFrame(basics)
        basics_frame.loc[:, 'asset_type'] = 'convertible'
        return basics_frame

    def _load_data(self, to_be_updated):
        """
            inner method
            accumulate equities , convertibles, etfs
            :return: AssetData
        """
        equity_frames = self._request_equities_basics(to_be_updated)
        convertible_frames = self._request_convertible_basics(to_be_updated)
        fund_frames = self._request_funds(to_be_updated)
        return AssetData(
            equities=equity_frames,
            convertibles=convertible_frames,
            funds=fund_frames,
        )

    def load_data(self):
        to_be_updated = self._update_assets()
        data = self._load_data(to_be_updated)
        return data

    def rerun(self):
        if len(self.missing['equity']):
            equity_frames = self._request_equities_basics(self.missing)
            left_equity = AssetData.equities = equity_frames
            self._writer.write(left_equity)
            self.rerun()

    def writer(self):
        asset_data = self.load_data()
        print('missing equity', self.missing)
        self._writer.write(asset_data)


if __name__ == '__main__':

    spider = AssetSpider()
    spider.writer()
