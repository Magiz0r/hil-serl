#include "home_profile.h"
#include <cassert>
#include <cmath>
#include <iostream>

int main() {
  using namespace hil_serl_home;
  constexpr double dt=.001;
  for (double delta : {0., .001, .2, .6, .8, 1.2, 1.28}) {
  const double seconds=duration(delta);
  assert(seconds>=6 && seconds<=12);
  if(delta<=.6) assert(seconds==6);
  if(delta==1.28) assert(std::abs(seconds-12)<1e-12);
  double previous=0, velocity=0, acceleration=0;
  double max_v=0,max_a=0,max_j=0;
  for (int i=0;i<=13000;++i) {
    const double q=delta*fraction(i*dt,seconds);
    const double v=(q-previous)/dt, a=(v-velocity)/dt, j=(a-acceleration)/dt;
    assert(std::isfinite(q) && q>=previous-1e-12 && q>=0 && q<=delta+1e-12);
    max_v=std::max(max_v,std::abs(v));max_a=std::max(max_a,std::abs(a));max_j=std::max(max_j,std::abs(j));
    previous=q;velocity=v;acceleration=a;
  }
  assert(fraction(0,seconds)==0 && fraction(.2,seconds)==0 && fraction(seconds+.201,seconds)==1);
  assert(max_v<=.2+1e-8 && max_a<=.1+1e-8 && max_j<=.2+1e-8);
  assert(std::abs(velocity)<1e-9 && std::abs(acceleration)<1e-9);
  std::cout << "profile_passed delta=" << delta << " seconds=" << seconds << " peak_velocity=" << max_v << " acceleration=" << max_a << " jerk=" << max_j << '\n';
  }
  assert(std::isnan(duration(1.281)) && std::isnan(duration(-1)) && std::isnan(duration(NAN)));
}
