import json,os,tempfile,unittest,sys
from pathlib import Path
import ezdxf
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from member_geometry import collect_segments,candidate_chains
from design_rules import validate_pattern
from shop_drawing import generate_job

class CoreTests(unittest.TestCase):
    def test_flat_chain(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x.dxf';d=ezdxf.new();m=d.modelspace();d.layers.add('3_Members')
            for a,b in [((0,0),(400,0)),((400,0),(700,0)),((700,0),(1000,0))]:m.add_line(a,b,dxfattribs={'layer':'3_Members'})
            d.saveas(p);segs=collect_segments(p);c=candidate_chains(segs,[(100,100)],{'section':'4 thk x45','length_mm':1000})[0]
            self.assertAlmostEqual(c['length_mm'],1000,places=6);self.assertEqual(c['section_info']['width_mm'],45)
    def test_design_validation(self):
        r={'standard':'TEST','hole_rules':[{'hole_diameter_mm':13.5,'min_pitch_mm':27,'min_edge_distance_mm':22}]}
        self.assertTrue(validate_pattern(r,500,[{'along_mm':30,'hole_diameter_mm':13.5,'edge_distance_mm':25},{'along_mm':70,'hole_diameter_mm':13.5,'edge_distance_mm':25}])['valid'])
        self.assertFalse(validate_pattern(r,500,[{'along_mm':30,'hole_diameter_mm':13.5,'edge_distance_mm':10}])['valid'])

class ReportingTests(unittest.TestCase):
    def test_inventory_counts_unique_backmarks(self):
        from shop_reporting import inventory_from_schedule
        schedule={'37':{'section':'L50x50x5','length_mm':2294},'44':{'section':'FLAT 4x45','length_mm':1128}}
        inv=inventory_from_schedule(schedule, None)
        self.assertEqual(inv['total_unique_shop_drawings_identified'],2)
        self.assertEqual(inv['currently_buildable_with_design_input'],0)

    def test_reference_generated_json_compare(self):
        from shop_reporting import normalize_shop_record, compare_sets
        ref=normalize_shop_record({'drawing_id':'429B44','source_backmark':'44','member':'FLAT 4x45','length_mm':1128,'qty':4,'per_piece':{'M12':8},'hole_diameters_mm':{'13.5':8}})
        gen=normalize_shop_record({'drawing_id':'429B44','backmark':'44','section':'FLAT 4x45','length_mm':1128.4,'qty':4,'ends':{'E1':{'holes':[{'along_mm':35,'hole_diameter_mm':13.5,'nominal_bolt_diameter_mm':12}]*8}}})
        result=compare_sets([ref],[gen],length_tol_mm=2)
        self.assertEqual(result['status_counts'].get('MATCH'),1)

if __name__=='__main__':unittest.main()
