import uuid
import json
from collections import OrderedDict
from meshtool.filters.base_filters import SaveFilter
import numpy
import collada

def deccolor(c):
    return (int(c[0] * 255) << 16) + (int(c[1] * 255) << 8) + int(c[2] * 255)



class ThreeJSDictGenerator(object):
    def __init__(self, mesh):
        self.mesh = mesh
        self.texture_placements = None
        self.unique_materials = {}

    def to_dict(self):
        outdict = {}
        outdict['metadata'] = {'version': 4.3,
                               'type': 'Object',
                               'generator': 'meshtool'}
        outdict['object'], outdict['geometries'] = self.getScene()
        outdict['materials'] = list(self.unique_materials.values())
        return outdict

    def save_to(self, filename):
        outputfile = open(filename, 'w')

        outdict = self.to_dict()
        outputfile.write(json.dumps(outdict, separators=(',', ':')))
        outputfile.close()


    def get_unique_material(self, material):
        mat = self.get_converted_material(material)
        key = ';'.join([str(v) for k, v in mat.items() if k != 'uuid'])

        mat = self.unique_materials.setdefault(key, mat)
        return mat['uuid']


    def get_converted_material(self, material):
        effect = material.effect

        attrs = OrderedDict()
        attrs['uuid'] = material.id
        attrs['name'] = material.name or ''
        if effect.shadingtype == 'lambert':
            attrs['type'] = 'MeshLambertMaterial'
        elif effect.shadingtype == 'phong' or effect.shadingtype == 'blinn':
            attrs['type'] = 'MeshPhongMaterial'
        else:
            attrs['type'] = 'MeshBasicMaterial'

        color_mapping = [('diffuse', 'color'),
                         ('ambient', 'ambient'),
                         ('specular', 'specular'),
                         ('emission', 'emissive')]
        for effect_attr, three_name in color_mapping:
            val = getattr(effect, effect_attr, None)
            if val is not None and not isinstance(val, collada.material.Map):
                attrs[three_name] = deccolor(val)

        float_mapping = [('shininess', 'shininess'),
                         ('transparency', 'opacity'),
                         ('reflectivity', 'reflectivity'),
                         ('index_of_refraction', 'refractionRatio')]

        for effect_attr, three_name in float_mapping:
            val = getattr(effect, effect_attr, None)
            if val is not None and not isinstance(val, collada.material.Map):
                attrs[three_name] = val
        attrs['transparent'] = attrs['opacity'] < 1
        map_mapping = [('diffuse', 'map'),
                       ('ambient', 'mapAmbient'),
                       ('specular', 'mapSpecular'),
                       ('bump_map', 'mapNormal')]
        for effect_attr, three_name in map_mapping:
            val = getattr(effect, effect_attr, None)
            if isinstance(val, collada.material.Map):
                attrs[three_name] = val.sampler.surface.image.id

        # transparency
        trans_color = effect.transparent
        if trans_color is not None:
            transparency = effect.transparency if effect.transparency is not None else 1
            if effect.opaque_mode == 'A_ONE':
                # Takes the transparency information from the colors alpha channel, where the value 1.0 is opaque.
                opacity = trans_color[3] * transparency

            elif effect.opaque_mode == 'RGB_ZERO':
                # Takes the transparency information from the colors red, green, and blue channels,
                # where the value 0.0 is opaque, with each channel modulated independently.

                # luminance is the function, based on the ISO/CIE color standards (see ITU-R Recommendation
                # BT.709-4), that averages the color channels into one value
                luminance = trans_color[0] * 0.212671 + trans_color[1] * 0.715160 + trans_color[2] * 0.072169
                opacity = 1.0 - luminance * transparency

            else:
                raise NotImplementedError

            attrs['opacity'] = opacity
            attrs['transparent'] = opacity < 1
        return attrs


    def serializeBoundPrimitives(self, primitives, geom_id):
        offset = 0
        indices_array = None
        positions_array = None
        normals_array = None
        for prim in primitives:
            if isinstance(prim, collada.polygons.BoundPolygons) or isinstance(prim, collada.polylist.BoundPolylist):
                prim = prim.triangleset()
            if positions_array is None:
                positions_array = prim.vertex
            else:
                positions_array = numpy.concatenate((positions_array, prim.vertex))

            if normals_array is None:
                normals_array = prim.normal
            else:
                normals_array = numpy.concatenate((normals_array, prim.normal))

            if indices_array is None:
                indices_array = prim.vertex_index
            else:
                indices_array = numpy.concatenate((indices_array, prim.vertex_index + offset))

            offset = len(positions_array)
        assert numpy.amax(indices_array) < len(positions_array), (numpy.amax(indices_array), len(positions_array))


        return {
            'uuid': geom_id,
            'type': 'BufferGeometry',
            'data': {
                'index': {
                    'type': 'Uint32Array',
                    'array': indices_array.flatten().tolist()
                },
                'attributes': {
                    'position':{
                        'itemSize': 3,
                        'type': 'Float32Array',
                        'array': positions_array.flatten().tolist()
                    },
                    'normal':{
                        'itemSize': 3,
                        'type': 'Float32Array',
                        'array': normals_array.flatten().tolist() if normals_array is not None else []
                    },
                }
            },
        }


    def getScene(self):
        children = []
        scale = self.mesh.assetInfo.unitmeter;
        object = {
            'uuid': str(uuid.uuid4()),
            'type': 'Scene',
            'matrix': [scale, 0, 0, 0,
                       0, scale, 0, 0,
                       0, 0, scale, 0,
                       0, 0, 0, 1],
            'children': children
        }

        matrix = numpy.identity(4)
        print(self.mesh.assetInfo.upaxis)
        #if self.mesh.assetInfo.upaxis == collada.asset.UP_AXIS.X_UP:
        #    r = collada.scene.RotateTransform(0, 1, 0, -90)
        #    matrix = r.matrix
        #elif self.mesh.assetInfo.upaxis == collada.asset.UP_AXIS.Z_UP:
        #    r = collada.scene.RotateTransform(1, 0, 0, -90)
        #    matrix = r.matrix


        primitives_by_mat = {}
        if self.mesh.scene is not None:
            for boundgeom in self.mesh.scene.objects('geometry'):
                for boundprim in boundgeom.primitives():
                    if isinstance(boundprim, collada.lineset.BoundLineSet):
                        continue
                    if boundprim.vertex is None or not len(boundprim.vertex):
                        continue
                    mat = self.get_unique_material(boundprim.material)
                    primitives = primitives_by_mat.setdefault(mat, [])
                    primitives.append(boundprim)

        geometries = []

        for mat, bound_prims in primitives_by_mat.items():
            geom_id = str(uuid.uuid4())
            children.append({
                'uuid':  str(uuid.uuid4()),
                'material': mat,
                'type': 'Mesh',
                'geometry': geom_id
            })
            geometries.append(self.serializeBoundPrimitives(bound_prims, geom_id))

        return object, geometries


def FilterGenerator():
    class ThreeJsSceneSaveFilter(SaveFilter):
        def __init__(self):
            super(ThreeJsSceneSaveFilter, self).__init__('save_threejs_scene',
                                                         'Saves a collada model in three.js 4.3 scene format')

        def apply(self, mesh, filename):
            # if os.path.exists(filename):
            #    raise FilterException('specified filename already exists')

            generator = ThreeJSDictGenerator(mesh)
            generator.save_to(filename)

            return mesh

    return ThreeJsSceneSaveFilter()


from meshtool.filters import factory

factory.register(FilterGenerator().name, FilterGenerator)
