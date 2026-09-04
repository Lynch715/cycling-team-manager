"""骑行力学：功率 <-> 速度。

全部使用 SI 单位（米、千克、秒、瓦、牛顿）。
本模块不依赖任何游戏概念，可独立测试。
"""

from __future__ import annotations

import math

G = 9.80665           # 重力加速度 m/s^2
RHO_SEA_LEVEL = 1.225  # 海平面空气密度 kg/m^3
DRIVETRAIN_EFF = 0.976  # 传动效率（腿部功率 -> 后轮功率）

# 数值积分下限：速度趋近 0 时 P/v 会发散，用一个物理上无意义但数值安全的地板
MIN_SPEED = 0.5


def air_density(altitude_m: float, temperature_c: float = 15.0) -> float:
    """按国际标准大气估算空气密度。

    高海拔山顶密度显著下降（阿尔卑斯 2000m 约为海平面的 82%），
    这会让爬坡时的风阻收益变小、但下坡极速变高，是真实存在的效应。
    """
    lapse = 0.0065                       # K/m，对流层温度递减率
    t_sea = temperature_c + 273.15
    t_alt = max(200.0, t_sea - lapse * altitude_m)
    pressure = 101325.0 * (t_alt / t_sea) ** 5.2559
    return pressure / (287.058 * t_alt)


def gravity_force(total_mass: float, grade: float) -> float:
    """重力沿坡道的分量。grade 为 tan(坡角)，如 0.08 表示 8%。"""
    return total_mass * G * math.sin(math.atan(grade))


def rolling_force(total_mass: float, grade: float, crr: float) -> float:
    """滚动阻力。"""
    return total_mass * G * math.cos(math.atan(grade)) * crr


def aero_force(speed: float, cda: float, rho: float, headwind: float = 0.0) -> float:
    """空气阻力。headwind > 0 为逆风，< 0 为顺风。

    注意用的是相对气流速度的平方并保留符号：强顺风且骑得慢时，
    空气会推着车手前进（阻力为负），这在真实的顺风冲刺里确实发生。
    """
    v_air = speed + headwind
    return 0.5 * rho * cda * v_air * abs(v_air)


def resistive_force(
    speed: float,
    total_mass: float,
    grade: float,
    cda: float,
    crr: float,
    rho: float = RHO_SEA_LEVEL,
    headwind: float = 0.0,
) -> float:
    """三项阻力之和（牛顿）。下坡时可以为负。"""
    return (
        gravity_force(total_mass, grade)
        + rolling_force(total_mass, grade, crr)
        + aero_force(speed, cda, rho, headwind)
    )


def power_required(
    speed: float,
    total_mass: float,
    grade: float,
    cda: float,
    crr: float,
    rho: float = RHO_SEA_LEVEL,
    headwind: float = 0.0,
) -> float:
    """以给定速度匀速前进所需的腿部功率（瓦）。

    下坡时可能为负，表示不蹬也能维持甚至加速；调用方通常应 max(0, ...)。
    """
    force = resistive_force(speed, total_mass, grade, cda, crr, rho, headwind)
    return force * speed / DRIVETRAIN_EFF


def steady_speed(
    power: float,
    total_mass: float,
    grade: float,
    cda: float,
    crr: float,
    rho: float = RHO_SEA_LEVEL,
    headwind: float = 0.0,
    hi: float = 45.0,
) -> float:
    """给定功率求平衡速度，二分法解三次方程。

    power_required 在 v>0 上单调递增（顺风时低速段除外），
    且 f(0) = -power <= 0、f(45 m/s) 远大于任何人类功率，故二分必然收敛。
    """
    lo = 0.0
    if power_required(hi, total_mass, grade, cda, crr, rho, headwind) < power:
        return hi
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if power_required(mid, total_mass, grade, cda, crr, rho, headwind) < power:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def advance_speed(
    speed: float,
    power: float,
    dt: float,
    total_mass: float,
    grade: float,
    cda: float,
    crr: float,
    rho: float = RHO_SEA_LEVEL,
    headwind: float = 0.0,
    max_speed: float = 40.0,
) -> float:
    """按牛顿第二定律推进一个时间步，返回新速度。

    用力的积分而不是直接取平衡速度，是为了让加速、起动、突围的
    "拉开差距需要时间" 这件事自然涌现，而不是靠额外规则去补。
    """
    v = max(speed, MIN_SPEED)
    propulsive = power * DRIVETRAIN_EFF / v
    drag = resistive_force(v, total_mass, grade, cda, crr, rho, headwind)
    # 旋转惯量：轮组让有效质量比静质量高约 1.5%
    accel = (propulsive - drag) / (total_mass * 1.015)
    new_v = v + accel * dt
    return min(max(new_v, MIN_SPEED), max_speed)
