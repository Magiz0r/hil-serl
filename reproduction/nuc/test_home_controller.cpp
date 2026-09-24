// Runs only against in-memory hardware interfaces in a network-none container.
#include "home_controller.cpp"
#include <cassert>
#include <iostream>
#include <pluginlib/class_loader.h>

int main(int argc,char** argv) {
  ros::init(argc,argv,"offline_home_controller_test");
  ros::NodeHandle node("/offline_home");
  pluginlib::ClassLoader<controller_interface::ControllerBase> loader(
      "controller_interface","controller_interface::ControllerBase");
  auto loaded=loader.createInstance("hil_serl_home/HomeController");
  assert(loaded);
  for(double delta : {.2, .6, .8, 1.2, 1.28, 1.4}) {
  hardware_interface::RobotHW hw;
  hardware_interface::PositionJointInterface positions;
  franka_hw::FrankaStateInterface states;
  franka::RobotState state;
  std::array<double,7> measured{},velocity{},effort{},commands{},goal{};
  std::vector<std::string> names;
  for (int i=0;i<7;++i) {
    names.push_back("fr3_joint"+std::to_string(i+1));
    state.q_d[i]=-.5+i*.05;
    measured[i]=state.q_d[i]+.01; // desired and measured intentionally differ
    goal[i]=state.q_d[i]+(i%2 ? delta : -delta);
    hardware_interface::JointStateHandle joint(names[i],&measured[i],&velocity[i],&effort[i]);
    positions.registerHandle(hardware_interface::JointHandle(joint,&commands[i]));
  }
  states.registerHandle(franka_hw::FrankaStateHandle("fr3_robot",state));
  hw.registerInterface(&positions);hw.registerInterface(&states);
  node.setParam("arm_id","fr3");node.setParam("joint_names",names);
  node.setParam("target_joint_positions",std::vector<double>(goal.begin(),goal.end()));
  hil_serl_home::HomeController controller;
  assert(controller.init(&hw,node));controller.starting(ros::Time(0));
  assert(commands==state.q_d);
  controller.update(ros::Time(0),ros::Duration(.03));
  controller.starting(ros::Time(0)); // franka_control resets at first period=0
  controller.update(ros::Time(0),ros::Duration(0));
  assert(commands==state.q_d);
  auto previous=commands;std::array<double,7> v{},a{};
  for(int tick=1;tick<=13000;++tick) {
    controller.update(ros::Time(tick*.001),ros::Duration(.001));
    for(int i=0;i<7;++i) {
      const double next_v=(commands[i]-previous[i])/.001;
      const double next_a=(next_v-v[i])/.001;
      const double jerk=(next_a-a[i])/.001;
      assert(std::abs(next_v)<=.2+1e-8 && std::abs(next_a)<=.1+1e-8 && std::abs(jerk)<=.2+1e-8);
      v[i]=next_v;a[i]=next_a;previous[i]=commands[i];
    }
    if(delta<=.6 && tick==6201)
      for(int i=0;i<7;++i) assert(std::abs(commands[i]-goal[i])<1e-12);
  }
  for(int i=0;i<7;++i) assert(std::abs(commands[i]-(delta>1.28 ? state.q_d[i] : goal[i]))<1e-12);
  }
  std::cout<<"controller_passed desired_start_and_bounded_derivatives\n";
}
