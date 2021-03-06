# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Mar 12 15:37:47 2019

@author: python
"""
from toolz import valmap
from collections import defaultdict, OrderedDict
from functools import partial
from finance.position import Position
from gateway.driver.data_portal import portal


class PositionTracker(object):
    """
        track the change of position
    """
    def __init__(self):
        self.positions = OrderedDict()
        self.record_closed_position = defaultdict(list)
        self._dirty_stats = True

    @staticmethod
    def _calculate_adjust_ratio(dividend):
        """
            股权登记日 ex_date
            股权除息日（为股权登记日下一个交易日）
            但是红股的到账时间不一致（制度是固定的）
            根据上海证券交易规则，对投资者享受的红股和股息实行自动划拨到账。股权（息）登记日为R日，除权（息）基准日为R+1日，
            投资者的红股在R+1日自动到账，并可进行交易，股息在R+2日自动到帐，
            其中对于分红的时间存在差异
            根据深圳证券交易所交易规则，投资者的红股在R+3日自动到账，并可进行交易，股息在R+5日自动到账，
            持股超过1年：税负5%;持股1个月至1年：税负10%;持股1个月以内：税负20%新政实施后，上市公司会先按照5%的最低税率代缴红利税
        """
        try:
            amount_ratio = (dividend['sid_bonus'] + dividend['sid_transfer']) / 10
            cash_ratio = dividend['bonus'] / 10
        except ZeroDivisionError:
            amount_ratio = 0.0
            cash_ratio = 0.0
        return amount_ratio, cash_ratio

    def handle_splits(self, dts):
        total_left_cash = 0
        dividends = portal.get_dividends(set(self.positions), dts)
        for asset, position in self.positions.items():
            # update last_sync_date
            position.inner_position.last_sync_date = dts
            try:
                dividend = dividends.loc[asset.sid, :]
            except KeyError:
                pass
            else:
                amount_ratio, cash_ratio = self._calculate_adjust_ratio(dividend)
                left_cash = position.handle_split(amount_ratio, cash_ratio)
                total_left_cash += left_cash
        return total_left_cash

    def _handle_transaction(self, transaction):
        asset = transaction.asset
        try:
            position = self.positions[asset]
        except KeyError:
            position = self.positions[asset] = Position(asset)
        cash_flow = position.update(transaction)
        # print('position -------------------status', position.amount, position.closed)
        if position.closed:
            dts = transaction.created_dt.strftime('%Y-%m-%d')
            self.record_closed_position[dts].append(position)
            print('end session closed positions', self.record_closed_position)
            del self.positions[asset]
        return cash_flow

    def handle_transactions(self, transactions):
        """执行完交易cash变动"""
        aggregate_cash_flow = 0.0
        if transactions:
            for txn in transactions:
                aggregate_cash_flow += self._handle_transaction(txn)
            self._dirty_stats = False
        return aggregate_cash_flow

    def synchronize(self):
        """
            a. sync last_sale_price of position (close price)
            b. update position return series
            c. update last_sync_date
        including : positions and closed position
        """
        sync_date_set = list(set([p.last_sync_date for p in self.positions.values()]))
        # print('sync_date', sync_date)
        if sync_date_set:
            assert len(sync_date_set) == 1, 'all positions must be sync on the same date'
            sync_date = sync_date_set[0]
            get_price = partial(portal.get_spot_value,
                                dts=sync_date,
                                frequency='daily',
                                field='close')
            closed_positions = self.record_closed_position[sync_date]
            print('synchronize closed_position', closed_positions)
            update_positions = set(closed_positions) | set(self.positions.values())
            print('synchronize update_positions', update_positions)
            for p in update_positions:
                p.inner_position.last_sync_price = get_price(asset=p.asset)
                # update position_returns
                p.calculate_returns()

    @staticmethod
    def retrieve_equity_rights(assets, dt):
        """
            配股机制有点复杂 ， freeze capital
            如果不缴纳款，自动放弃到期除权相当于亏损,在股权登记日卖出，一般的配股缴款起止日为5个交易日
        """
        rights = portal.get_rights(assets, dt)
        return rights

    def get_positions(self):
        # return protocol mappings
        protocols = valmap(lambda x: x.protocol, self.positions)
        return protocols


__all__ = ['PositionTracker']
