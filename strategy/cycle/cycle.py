# -*- coding : utf-8 -*-
"""
Created on Tue Mar 12 15:37:47 2019

@author: python
"""


class Cycle(object):
    """
        基于大类过滤
        标的 --- 按照市值排明top 10% 标的标的集合 --- 度量周期的联动性
        a 计算每个月的月度收益率，筛选出10%集合 / 12的个数，
        b 获取每个月的集合 --- 作为当月的强周期集合
        c 基于技术指标等技术获取对应的标的
    """



