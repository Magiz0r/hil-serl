import hashlib
import json
from pathlib import Path
import unittest


class RotationControllerTests(unittest.TestCase):
    def test_only_orientation_filter_and_independent_class_differ_from_pinned_controller(self):
        root=Path(__file__).parents[1]/'nuc'
        provenance=json.loads((root/'rotation_controller_provenance.json').read_text())
        for name,expected in provenance['changes'].items():
            data=(root/name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(),expected['sha256'])
            original=data.decode().replace('ResponsiveCartesianImpedanceController','CartesianImpedanceController')
            if name.endswith('.cpp'):
                self.assertEqual(original.count('orientation_d_.slerp(0.02, orientation_d_target_)'),1)
                original=original.replace('#include "rotation_controller.h"','#include <serl_franka_controllers/cartesian_impedance_controller.h>')
                original=original.replace('orientation_d_.slerp(0.02, orientation_d_target_)','orientation_d_.slerp(filter_params_, orientation_d_target_)')
            self.assertEqual(hashlib.sha256(original.encode()).hexdigest(),expected['base_sha256'])

    def test_filter_step_response_is_monotone_and_settles_faster(self):
        # Quaternion slerp with a fixed target gives this exact recurrence for
        # the remaining shortest-arc angle; this does not model robot dynamics.
        residual={.005:1.,.02:1.}
        for _ in range(100):
            for coefficient in residual:
                previous=residual[coefficient]
                residual[coefficient]*=1-coefficient
                self.assertGreater(residual[coefficient],0.)
                self.assertLess(residual[coefficient],previous)
        self.assertGreater(1-residual[.02],2*(1-residual[.005]))
