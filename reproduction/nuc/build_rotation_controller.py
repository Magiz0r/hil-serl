#!/usr/bin/env python3
"""Build the isolated rotation plugin in a network-none pinned-image container.

Run on NUC host after deploying these sources. No robot, ROS master, original
image, or existing container is started by this build. Artifacts get a new path.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone

ROOT=Path('/home/tasl/hil_serl_runtime_20260918')
IMAGE='sha256:d56016766146580ba8e7e2e0d4fa191a7f902ec8dc54a7ae06387cb1d9a7f151'

def main():
    source=ROOT/'source'
    expected=json.loads((ROOT/'source-sha256.json').read_text())
    for name in ('rotation_controller.cpp','rotation_controller.h','rotation_controller_provenance.json','build_rotation_controller.py'):
        assert hashlib.sha256((source/name).read_bytes()).hexdigest()==expected[name]
    own=json.loads(subprocess.check_output(['docker','inspect','hil-serl-fr3-hold-20260918'],text=True))[0]
    assert own['Image']==IMAGE and not own['State']['Running']
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    relative=Path('rotation-controller')/stamp
    directory=ROOT/'state'/relative
    directory.mkdir(parents=True,exist_ok=False)
    runtime=Path('/hil-serl-state')/relative
    pkg=directory/'ws/src/hil_serl_rotation'
    pkg.mkdir(parents=True)
    for name in ('rotation_controller.cpp','rotation_controller.h'):
        (pkg/name).write_bytes((source/name).read_bytes())
    (pkg/'package.xml').write_text('''<package format="2"><name>hil_serl_rotation</name><version>0.1.0</version>
<description>Independent SERL orientation-response variant</description><maintainer email="local@example.invalid">HIL SERL</maintainer>
<license>MIT</license><buildtool_depend>catkin</buildtool_depend><depend>serl_franka_controllers</depend>
<depend>controller_interface</depend><depend>franka_hw</depend><depend>hardware_interface</depend><depend>pluginlib</depend><depend>roscpp</depend>
<export><controller_interface plugin="${prefix}/rotation_plugin.xml"/></export></package>''')
    (pkg/'rotation_plugin.xml').write_text('''<library path="lib/libhil_serl_rotation"><class name="hil_serl_rotation/ResponsiveCartesianImpedanceController"
type="serl_franka_controllers::ResponsiveCartesianImpedanceController" base_class_type="controller_interface::ControllerBase">
<description>SERL controller with independent faster orientation smoothing</description></class></library>''')
    (pkg/'test_plugin.cpp').write_text('''#include <pluginlib/class_loader.h>
#include <controller_interface/controller_base.h>
#include <cassert>
#include <iostream>
#include <ros/ros.h>
int main(int argc,char** argv){ros::init(argc,argv,"rotation_plugin_test");
ros::NodeHandle node;pluginlib::ClassLoader<controller_interface::ControllerBase> loader("controller_interface","controller_interface::ControllerBase");
auto controller=loader.createInstance("hil_serl_rotation/ResponsiveCartesianImpedanceController");assert(controller);
std::cout<<"rotation_plugin_load_passed\\n";}
''')
    (pkg/'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.10)
project(hil_serl_rotation)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_INSTALL_RPATH_USE_LINK_PATH TRUE)
set(CMAKE_INSTALL_RPATH /opt/venv/franka-0.18.0/franka_catkin_ws/libfranka/build)
find_package(catkin REQUIRED COMPONENTS controller_interface serl_franka_controllers franka_hw hardware_interface pluginlib roscpp)
find_package(Franka REQUIRED)
find_package(Eigen3 REQUIRED)
catkin_package(LIBRARIES hil_serl_rotation)
include_directories(${catkin_INCLUDE_DIRS} ${Franka_INCLUDE_DIRS} ${EIGEN3_INCLUDE_DIRS})
add_library(hil_serl_rotation rotation_controller.cpp)
target_link_libraries(hil_serl_rotation ${catkin_LIBRARIES} ${Franka_LIBRARIES})
add_executable(test_plugin test_plugin.cpp)
target_compile_options(test_plugin PRIVATE -UNDEBUG)
target_link_libraries(test_plugin ${catkin_LIBRARIES})
install(TARGETS hil_serl_rotation LIBRARY DESTINATION ${CATKIN_PACKAGE_LIB_DESTINATION})
install(FILES rotation_plugin.xml DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION})
''')
    (directory/'test_plugin.py').write_text('''import os,subprocess,time,xmlrpc.client
os.environ.update(ROS_MASTER_URI='http://127.0.0.1:11321',ROS_IP='127.0.0.1',ROS_HOSTNAME='127.0.0.1',ROS_HOME='/tmp/ros',ROS_LOG_DIR='/tmp/ros/log')
master=subprocess.Popen(['roscore','-p','11321'],stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
try:
 deadline=time.monotonic()+8
 while True:
  try:
   assert xmlrpc.client.ServerProxy('http://127.0.0.1:11321').getPid('/rotation_plugin_test')[0]==1
   break
  except (OSError,AssertionError):
   if time.monotonic()>deadline:raise RuntimeError('offline ROS master did not start')
   time.sleep(.1)
 subprocess.run(['''+repr(str(runtime/'ws/devel/lib/hil_serl_rotation/test_plugin'))+'''],check=True,timeout=15)
finally:
 master.terminate();master.wait(timeout=8)
''')
    name='hil-serl-build-rotation-'+stamp.lower()
    command=['docker','run','--rm','--init','--name',name,
             '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
             '--user',str(os.getuid())+':'+str(os.getgid()),'--env','HOME=/tmp',
             '--tmpfs','/tmp:rw,nosuid,nodev,size=268435456,mode=1777',
             '--mount','type=bind,source='+str(ROOT/'state')+',target=/hil-serl-state',IMAGE,'bash','-c',
             'source /opt/venv/franka-0.18.0/franka_catkin_ws/devel/setup.bash && '+
             'catkin_make -C '+str(runtime/'ws')+' -j2 -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX='+str(runtime/'install')+
             ' install && source '+str(runtime/'install/setup.bash')+' && python3 '+str(runtime/'test_plugin.py')]
    try:
        subprocess.run(command,check=True,timeout=120)
    finally:
        probe=subprocess.run(['docker','inspect',name],text=True,capture_output=True)
        if probe.returncode==0:
            live=json.loads(probe.stdout)[0]
            assert live['Image']==IMAGE and live['HostConfig']['NetworkMode']=='none'
            subprocess.run(['docker','stop','--timeout','3',live['Id']],check=True,timeout=8)
    artifacts={str(path.relative_to(ROOT/'state')):hashlib.sha256(path.read_bytes()).hexdigest()
               for path in (directory/'install').rglob('*') if path.is_file()}
    result=dict(prefix=str(runtime/'install'),files=artifacts,source_sha256={n:expected[n] for n in
        ('rotation_controller.cpp','rotation_controller.h','rotation_controller_provenance.json','build_rotation_controller.py')})
    (directory/'artifacts.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()
