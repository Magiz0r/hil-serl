FROM remote-teleop-serl:2026-09-01-soft

LABEL org.opencontainers.image.title="hil-serl-fr3"
LABEL org.opencontainers.image.description="HIL-SERL ROS1 server with read-safe Robotiq RS485 backend"

COPY reproduction/nuc/requirements.txt /tmp/hil-serl-requirements.txt
COPY reproduction/nuc/wheels /tmp/hil-serl-wheels
RUN python3 -m pip install --no-cache-dir \
        --no-index \
        --find-links /tmp/hil-serl-wheels \
        --target /opt/hil-serl-python \
        -r /tmp/hil-serl-requirements.txt

COPY serl_robot_infra/robot_servers /opt/hil-serl/robot_servers

ENV PYTHONPATH=/opt/hil-serl-python:/opt/hil-serl
WORKDIR /opt/hil-serl/robot_servers

# Building or creating this image cannot acquire FCI or move hardware.
CMD ["sleep", "infinity"]
