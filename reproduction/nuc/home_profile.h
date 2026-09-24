#pragma once
#include <algorithm>
#include <cmath>
#include <limits>
#include "home_profile_limits.h"  // generated from home_profile.json at build time

namespace hil_serl_home {
inline double duration(double delta) {
  if (!std::isfinite(delta) || delta < 0) return std::numeric_limits<double>::quiet_NaN();
  const double seconds = std::max({kMinimumDuration, 1.875*delta/kMaxVelocity,
      std::sqrt((10/std::sqrt(3.0))*delta/kMaxAcceleration),
      std::cbrt(60*delta/kMaxJerk)});
  return seconds <= kMaximumDuration+1e-12 ? std::min(seconds,kMaximumDuration)
                                        : std::numeric_limits<double>::quiet_NaN();
}
// Zero velocity and acceleration at both ends. Peak ds/dt = 1.875/T.
inline double fraction(double elapsed, double seconds) {
  const double s = std::clamp((elapsed - kHold) / seconds, 0.0, 1.0);
  return s*s*s*(10.0 + s*(-15.0 + 6.0*s));
}
}
