// Isolated Home plugin; does not replace any upstream controller library.
#include "home_profile.h"
#include <array>
#include <cmath>
#include <memory>
#include <string>
#include <vector>
#include <controller_interface/multi_interface_controller.h>
#include <hardware_interface/joint_command_interface.h>
#include <franka_hw/franka_state_interface.h>
#include <pluginlib/class_list_macros.h>
#include <ros/ros.h>

namespace hil_serl_home {
class HomeController : public controller_interface::MultiInterfaceController<
    hardware_interface::PositionJointInterface, franka_hw::FrankaStateInterface> {
 public:
  bool init(hardware_interface::RobotHW* hw, ros::NodeHandle& node) override {
    auto* positions = hw->get<hardware_interface::PositionJointInterface>();
    auto* states = hw->get<franka_hw::FrankaStateInterface>();
    std::vector<std::string> names;
    std::vector<double> goal;
    std::string arm;
    if (!positions || !states || !node.getParam("arm_id", arm) || arm != "fr3" ||
        !node.getParam("joint_names", names) || names.size()!=7 ||
        !node.getParam("target_joint_positions", goal) || goal.size()!=7) {
      ROS_ERROR("HomeController: invalid interfaces or parameters"); return false;
    }
    try {
      state_ = std::make_unique<franka_hw::FrankaStateHandle>(states->getHandle(arm+"_robot"));
      for (size_t i=0; i<7; ++i) {
        if (!std::isfinite(goal[i]) || names[i] != arm+"_joint"+std::to_string(i+1)) return false;
        joints_.push_back(positions->getHandle(names[i]));
        goal_[i]=goal[i];
      }
    } catch (const hardware_interface::HardwareInterfaceException& e) {
      ROS_ERROR_STREAM("HomeController: " << e.what()); return false;
    }
    return true;
  }
  void starting(const ros::Time&) override {
    // Continue from the robot's desired joints, not a potentially offset
    // measured position. franka_control calls starting again at period=0
    // with the first state of the newly opened position-control session.
    initial_ = state_->getRobotState().q_d;
    double delta=0.;
    for (size_t i=0; i<7; ++i) delta=std::max(delta,std::abs(goal_[i]-initial_[i]));
    duration_=duration(delta);
    if (!std::isfinite(duration_)) ROS_ERROR("HomeController: path exceeds trajectory limits; holding start");
    elapsed_ = 0.;
    for (size_t i=0; i<7; ++i) joints_[i].setCommand(initial_[i]);
  }
  void update(const ros::Time&, const ros::Duration& period) override {
    elapsed_ += period.toSec();
    const double s=std::isfinite(duration_) ? fraction(elapsed_,duration_) : 0.;
    for (size_t i=0; i<7; ++i)
      joints_[i].setCommand(initial_[i]+s*(goal_[i]-initial_[i]));
  }
 private:
  std::unique_ptr<franka_hw::FrankaStateHandle> state_;
  std::vector<hardware_interface::JointHandle> joints_;
  std::array<double,7> initial_{}, goal_{};
  double elapsed_=0., duration_=kMaximumDuration;
};
}
PLUGINLIB_EXPORT_CLASS(hil_serl_home::HomeController, controller_interface::ControllerBase)
