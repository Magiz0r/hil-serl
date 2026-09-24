#!/usr/bin/env python3
"""Build the isolated Home plugin in a network-none pinned-image container.

Run on NUC host after deploying these sources. No robot, ROS master, original
image, or existing container is started by this build. Artifacts get a new path.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone
from home_profile import cpp_header

ROOT=Path('/home/tasl/hil_serl_runtime_20260918')
IMAGE='sha256:d56016766146580ba8e7e2e0d4fa191a7f902ec8dc54a7ae06387cb1d9a7f151'
BUILD_SOURCES=('home_controller.cpp','home_profile.h','home_profile.py','home_profile.json',
               'test_home_profile.cpp','test_home_controller.cpp','build_home_controller.py')

def main():
    source=ROOT/'source'
    expected=json.loads((ROOT/'source-sha256.json').read_text())
    for name in BUILD_SOURCES:
        assert hashlib.sha256((source/name).read_bytes()).hexdigest()==expected[name]
    own=json.loads(subprocess.check_output(['docker','inspect','hil-serl-fr3-hold-20260918'],text=True))[0]
    assert own['Image']==IMAGE and not own['State']['Running']
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    relative=Path('home-controller')/stamp
    directory=ROOT/'state'/relative
    directory.mkdir(parents=True,exist_ok=False)
    runtime=Path('/hil-serl-state')/relative
    pkg=directory/'ws/src/hil_serl_home'
    pkg.mkdir(parents=True)
    for name in ('home_controller.cpp','home_profile.h','test_home_profile.cpp','test_home_controller.cpp'):
        (pkg/name).write_bytes((source/name).read_bytes())
    (pkg/'home_profile_limits.h').write_text(cpp_header())
    (pkg/'package.xml').write_text('''<package format="2"><name>hil_serl_home</name><version>0.1.0</version>
<description>Isolated smooth Home controller</description><maintainer email="local@example.invalid">HIL SERL</maintainer>
<license>MIT</license><buildtool_depend>catkin</buildtool_depend><depend>controller_interface</depend>
<depend>franka_hw</depend><depend>hardware_interface</depend><depend>pluginlib</depend><depend>roscpp</depend>
<export><controller_interface plugin="${prefix}/home_plugin.xml"/></export></package>''')
    (pkg/'home_plugin.xml').write_text('''<library path="lib/libhil_serl_home"><class name="hil_serl_home/HomeController"
type="hil_serl_home::HomeController" base_class_type="controller_interface::ControllerBase">
<description>Smooth fixed joint Home</description></class></library>''')
    (pkg/'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.10)
project(hil_serl_home)
set(CMAKE_CXX_STANDARD 17)
find_package(catkin REQUIRED COMPONENTS controller_interface franka_hw hardware_interface pluginlib roscpp)
find_package(Franka REQUIRED)
catkin_package(LIBRARIES hil_serl_home)
include_directories(${catkin_INCLUDE_DIRS} ${Franka_INCLUDE_DIRS})
add_library(hil_serl_home home_controller.cpp)
target_link_libraries(hil_serl_home ${catkin_LIBRARIES} ${Franka_LIBRARIES})
add_executable(test_home_profile test_home_profile.cpp)
target_compile_options(test_home_profile PRIVATE -UNDEBUG)
add_executable(test_home_controller test_home_controller.cpp)
target_link_libraries(test_home_controller ${catkin_LIBRARIES} ${Franka_LIBRARIES})
target_compile_options(test_home_controller PRIVATE -UNDEBUG)
install(TARGETS hil_serl_home LIBRARY DESTINATION ${CATKIN_PACKAGE_LIB_DESTINATION})
install(FILES home_plugin.xml DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION})
''')
    command=['docker','run','--rm','--init','--name','hil-serl-build-home-'+stamp.lower(),
             '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
             '--user',str(os.getuid())+':'+str(os.getgid()),'--env','HOME=/tmp',
             '--tmpfs','/tmp:rw,nosuid,nodev,size=268435456,mode=1777',
             '--mount','type=bind,source='+str(ROOT/'state')+',target=/hil-serl-state',IMAGE,'bash','-c',
             'source /opt/venv/franka-0.18.0/franka_catkin_ws/devel/setup.bash && '+
             'catkin_make -C '+str(runtime/'ws')+' -j2 -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX='+str(runtime/'install')+
             ' install && '+str(runtime/'ws/devel/lib/hil_serl_home/test_home_profile')]
    subprocess.run(command,check=True,timeout=180)
    artifacts={str(path.relative_to(ROOT/'state')):hashlib.sha256(path.read_bytes()).hexdigest()
               for path in (directory/'install').rglob('*') if path.is_file()}
    result=dict(prefix=str(runtime/'install'),files=artifacts,source_sha256={n:expected[n] for n in BUILD_SOURCES})
    (directory/'artifacts.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()
