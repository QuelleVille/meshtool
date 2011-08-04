from meshtool.args import *
from meshtool.filters.base_filters import *
import inspect
import numpy
import networkx as nx
from itertools import izip, chain, repeat, imap
import datetime
import math
import __builtin__
import heapq
from render_utils import renderVerts, renderCharts
from graph_utils import astar_path, dfs_interior_nodes
import gc

if not 'set' in __builtin__.__dict__:
    import sets
    set = sets.Set

#after python2.5, uniqu1d was renamed to unique
args, varargs, keywords, defaults = inspect.getargspec(numpy.unique)
if 'return_inverse' not in args:
    numpy.unique = numpy.unique1d

def timer():
    begintime = datetime.datetime.now()
    while True:
        curtime = datetime.datetime.now()
        yield (curtime-begintime)
        begintime = curtime
         
def calcPerimeter(pts):
    dx = pts[:,0,0]-pts[:,1,0]
    dy = pts[:,0,1]-pts[:,1,1]
    dz = pts[:,0,2]-pts[:,1,2]
    return numpy.sum(numpy.sqrt(dx*dx + dy*dy + dz*dz))

def v3dist(pt1, pt2):
    d = pt1 - pt2
    return math.sqrt(d[0]*d[0] + d[1]*d[1] + d[2]*d[2])

def array_mult(arr1, arr2):
    return arr1[:,0]*arr2[:,0] + arr1[:,1]*arr2[:,1] + arr2[:,2]*arr1[:,2]
def array_dot(arr1, arr2):
    return numpy.sqrt( array_mult(arr1, arr2) )

def calcFitError(pts):
    # this computes the outer product of each vector
    # in the array, element-wise, then sums up the 3x3
    # matrices
    #  A = sum{v_i * v_i^T} 
    A = numpy.sum(pts[...,None] * pts[:,None,:], 0)
    
    # b = mean(v_i)
    b = numpy.mean(pts, 0)

    # Z = A - (b * b^T) / c
    Z = A - numpy.outer(b,b) / len(pts)
    
    # n (normal of best fit plane) is the eigenvector
    # corresponding to the minimum eigenvalue
    eigvals, eigvecs = numpy.linalg.eig(Z)
    n = eigvecs[numpy.argmin(eigvals)]
    
    # d (scalar offset of best fit plane) = -n^T * b / c
    d = numpy.inner(-n, b) / len(pts)
    
    # final error is the square of the mean distance of each point to the plane
    mean_dist = numpy.mean(array_mult(n[None,:].repeat(len(pts), axis=0), pts) + d)
    Efit = mean_dist * mean_dist
    
    return Efit

def tri_surface_area(arr):
    crosses = numpy.cross(arr[:,0] - arr[:,1], arr[:,0] - arr[:,2])
    return numpy.sum(array_dot(crosses, crosses) / 2.0)

def begin_operation():
    gc.disable()
def end_operation():
    gc.enable()

def sandler_simplify(mesh):
    all_vertices = []
    all_indices = []
    vertex_offset = 0
    t = timer()
    
    print 'building aggregated vertex and triangle list...',
    begin_operation()
    for boundgeom in mesh.scene.objects('geometry'):
        for boundprim in boundgeom.primitives():
            all_vertices.append(boundprim.vertex)
            all_indices.append(boundprim.vertex_index + vertex_offset)
            vertex_offset += len(boundprim.vertex)
    for boundcontroller in mesh.scene.objects('controller'):
        boundgeom = boundcontroller.geometry
        for boundprim in boundgeom.primitives():
            all_vertices.append(boundprim.vertex)
            all_indices.append(boundprim.vertex_index + vertex_offset)
            vertex_offset += len(boundprim.vertex)
            
    all_vertices = numpy.concatenate(all_vertices)
    all_indices = numpy.concatenate(all_indices)
    end_operation()
    print next(t)
    
    print 'uniqifying the list...',
    begin_operation()
    unique_data, index_map = numpy.unique(all_vertices.view([('',all_vertices.dtype)]*all_vertices.shape[1]), return_inverse=True)
    all_vertices = unique_data.view(all_vertices.dtype).reshape(-1,all_vertices.shape[1])
    all_indices = index_map[all_indices]
    
    #scale to known range so error values are normalized
    all_vertices[:,0] -= numpy.min(all_vertices[:,0])
    all_vertices[:,1] -= numpy.min(all_vertices[:,1])
    all_vertices[:,2] -= numpy.min(all_vertices[:,2])
    all_vertices *= 1000.0 / numpy.max(all_vertices)
    
    end_operation()
    print next(t)
    
    print 'building vertex vertices...',
    begin_operation()
    vertexgraph = nx.Graph()
    vertexgraph.add_nodes_from(xrange(len(all_vertices)))
    end_operation()
    print next(t)
    
    print 'building vertex edges...',
    begin_operation()
    vertexgraph.add_edges_from(( (edge[0], edge[1], {facenum:True})
                                 for facenum, edge in
                                 enumerate(all_indices[:,(0,1)]) ))
    vertexgraph.add_edges_from(( (edge[0], edge[1], {facenum:True})
                                 for facenum, edge in
                                 enumerate(all_indices[:,(0,2)]) ))
    vertexgraph.add_edges_from(( (edge[0], edge[1], {facenum:True})
                                 for facenum, edge in
                                 enumerate(all_indices[:,(1,2)]) ))
    end_operation()
    print next(t)
    
    print 'number of connected components in vertex graph =', nx.algorithms.components.connected.number_connected_components(vertexgraph)
    
    print 'building face vertices...',
    begin_operation()
    facegraph = nx.Graph()
    facegraph.add_nodes_from(( (i, {'tris':[tri], 
                                    'edges':set([tuple(sorted([tri[0], tri[1]])),
                                                 tuple(sorted([tri[1], tri[2]])),
                                                 tuple(sorted([tri[0], tri[2]]))])})
                               for i, tri in
                               enumerate(all_indices) ))
    end_operation()
    print next(t)
    
    print 'building face edges...',
    begin_operation()
    for e in vertexgraph.edges_iter(data=True):
        v1, v2, adjacent_faces = e
        adjacent_faces = adjacent_faces.keys()
        if len(adjacent_faces) == 2:
            facegraph.add_edge(adjacent_faces[0], adjacent_faces[1])
    end_operation()
    print next(t)
    
    merge_priorities = []
    maxerror = 0
    
    print 'calculating error...',
    begin_operation()
    for v1, v2 in facegraph.edges_iter():
        edges1 = facegraph.node[v1]['edges']
        edges2 = facegraph.node[v2]['edges']
        merged = numpy.array(list(edges1.symmetric_difference(edges2)))
        if len(merged) > 0:
            error = calcPerimeter(all_vertices[merged])**2
            error += calcFitError(all_vertices[merged.flatten()])
            if error > maxerror: maxerror = error
            merge_priorities.append((error, (v1, v2)))
    end_operation()
    print next(t)
        
    print 'creating priority queue...',
    begin_operation()
    heapq.heapify(merge_priorities)
    end_operation()
    print next(t)
    
    print 'merging charts...',
    begin_operation()
    node_count = len(all_indices)
    while len(merge_priorities) > 0:
        (error, (face1, face2)) = heapq.heappop(merge_priorities)
        
        #this can happen if we have already merged one of these
        if face1 not in facegraph or face2 not in facegraph:
            continue
        
        # if the number of corners of the merged face is less than 3, disqualify it
        # where a "corner" is defined as a vertex with at least 3 adjacent faces
        edges1 = facegraph.node[face1]['edges']
        edges2 = facegraph.node[face2]['edges']
        combined_edges = edges1.symmetric_difference(edges2)

        combined_vertices = set(chain.from_iterable(combined_edges))
        corners = set()
        for v in combined_vertices:
            adjacent = set()
            for (vv1, vv2, adj_dict) in vertexgraph.edges_iter(v, data=True):
                adjacent.update(adj_dict.keys())
            if len(adjacent) >= 3:
                corners.add(v)
        if len(corners) < 3:
            continue
        
        logrel = math.log(1 + error) / math.log(1 + maxerror)
        if logrel > 0.9:
            break
        #print 'error', error, 'maxerror', maxerror, 'logrel', logrel, 'merged left', len(merge_priorities), 'numfaces', len(facegraph)
        
        combined_tris = facegraph.node[face1]['tris'] + facegraph.node[face2]['tris']
        
        newface = node_count
        node_count += 1
        
        edges_to_add = []
        topush = []
        
        adj_faces = set(facegraph.neighbors(face1))
        adj_faces = adj_faces.union(set(facegraph.neighbors(face2)))
        adj_faces.remove(face1)
        adj_faces.remove(face2)
        
        for otherface in adj_faces:
            otheredges = facegraph.node[otherface]['edges']
            commonedges = combined_edges.intersection(otheredges)
            
            connected_components_graph = nx.from_edgelist(commonedges)
            if nx.algorithms.components.connected.number_connected_components(connected_components_graph) > 1:
                continue

            edges_to_add.append((newface, otherface))
            
            merged = numpy.array(list(combined_edges.symmetric_difference(otheredges)))
            if len(merged) > 0:
                error = calcPerimeter(all_vertices[merged])**2
                error += calcFitError(all_vertices[merged.flatten()])
                topush.append((error, (newface, otherface)))

        facegraph.add_node(newface, corners=corners, tris=combined_tris, edges=combined_edges)        
        facegraph.add_edges_from(edges_to_add)
        for p in topush:
            if p[0] > maxerror: maxerror = p[0]
            heapq.heappush(merge_priorities, p)

        for v in combined_vertices:
            edges = vertexgraph.edges(v, data=True)
            for (vv1, vv2, facedata) in edges:
                if face1 in facedata or face2 in facedata:
                    if face1 in facedata:
                        del facedata[face1]
                    if face2 in facedata:
                        del facedata[face2]
                    facedata[newface] = True
                    vertexgraph.add_edge(vv1, vv2, attr_dict=facedata)

        facegraph.remove_node(face1)
        facegraph.remove_node(face2)

    end_operation()
    print next(t)
    
    print 'final number of faces =', len(facegraph)
    print 'final number of connected components =', nx.algorithms.components.connected.number_connected_components(facegraph)
    
    print 'updating corners...',
    begin_operation()
    for face, facedata in facegraph.nodes_iter(data=True):
        edges = facegraph.node[face]['edges']
        vertices = set(chain.from_iterable(edges))
        corners = set()
        for v in vertices:
            adjacent = set()
            for (vv1, vv2, adj_dict) in vertexgraph.edges_iter(v, data=True):
                adjacent.update(adj_dict.keys())
            if len(adjacent) >= 3:
                corners.add(v)
        facegraph.add_node(face, corners=corners)
    end_operation()
    print next(t)
    
    print 'computing distance between points',
    begin_operation()
    for v1, v2 in vertexgraph.edges_iter():
        vertexgraph.add_edge(v1, v2, distance=v3dist(all_vertices[v1],all_vertices[v2]))
    end_operation()
    print next(t)
    
    print 'straightening chart boundaries...',
    begin_operation()
    for (face1, face2) in facegraph.edges_iter():
        
        #can't straighten the border of a single triangle
        tris1 = facegraph.node[face1]['tris']
        tris2 = facegraph.node[face2]['tris']
        if len(tris1) <= 1 or len(tris2) <= 1:
            continue
        
        edges1 = facegraph.node[face1]['edges']
        edges2 = facegraph.node[face2]['edges']
        combined_edges = edges1.symmetric_difference(edges2)
        shared_edges = edges1.intersection(edges2)
        #dont bother trying to straighten a single edge
        if len(shared_edges) == 1:
            continue
        shared_vertices = set(chain.from_iterable(shared_edges))

        corners1 = facegraph.node[face1]['corners']
        corners2 = facegraph.node[face2]['corners']
        combined_corners = corners1.intersection(corners2).intersection(shared_vertices)
        
        if len(combined_corners) < 1 or len(combined_corners) > 2:
            continue
        
        giveup = False
        if len(combined_corners) == 2:
            start_path, end_path = combined_corners
        elif len(combined_corners) == 1:
            pt2edge = {}
            for src, dest in shared_edges:
                srclist = pt2edge.get(src, [])
                srclist.append((src, dest))
                pt2edge[src] = srclist
                dstlist = pt2edge.get(dest, [])
                dstlist.append((src, dest))
                pt2edge[dest] = dstlist
            start_path = combined_corners.pop()
            curpt = start_path
            shared_path = []
            while curpt in pt2edge:
                edge = pt2edge[curpt][0]
                sanedge = edge
                if edge[0] != curpt:
                    sanedge = (edge[1], edge[0])
                nextpt = sanedge[1]
                shared_path.append(sanedge)
                nextopts = pt2edge[nextpt]
                if edge not in nextopts:
                    giveup = True
                    break
                nextopts.remove(edge)
                if len(nextopts) > 0:
                    pt2edge[nextpt] = nextopts
                else:
                    del pt2edge[nextpt]
                curpt = nextpt
            
            end_path = shared_path[-1][1] 
        if giveup:
            continue
        
        edges1 = edges1.symmetric_difference(shared_edges)
        edges2 = edges2.symmetric_difference(shared_edges)
        stop_nodes = set(chain.from_iterable(combined_edges))
        combined_tris = tris1 + tris2
        constrained_set = set(chain.from_iterable(combined_tris))
        
        try:
            straightened_path = astar_path(vertexgraph, start_path, end_path,
                                           heuristic=lambda x,y: v3dist(all_vertices[x], all_vertices[y]),
                                           weight='distance', subset=constrained_set, exclude=stop_nodes)
        except nx.exception.NetworkXNoPath:
            continue
        
        # if we already have the shortest path, nothing to do
        if set(shared_vertices) == set(straightened_path):
            continue
        
        new_combined_edges = []
        for i in range(len(straightened_path)-1):
            new_combined_edges.append(tuple(sorted((straightened_path[i], straightened_path[i+1]))))
        new_combined_edges = set(new_combined_edges)
        new_edges1 = edges1.symmetric_difference(new_combined_edges)
        new_edges2 = edges2.symmetric_difference(new_combined_edges)
        
        # This can happen if the shortest path actually encompasses
        # the smaller face, but this would be equivalent to merging the
        # two faces. If we didn't merge these two in the previous step,
        # it was because the cost was too high or it would violate one of
        # the constraints, so just ignore this 
        if len(new_edges1) == 0 or len(new_edges2) == 0:
            continue
        
        boundary1 = set(chain.from_iterable(new_edges1))
        boundary2 = set(chain.from_iterable(new_edges2))
        boundary = boundary1.union(boundary2).union(straightened_path)
        
        vertexset1 = boundary1.difference(straightened_path)
        vertexset2 = boundary2.difference(straightened_path)
        
        allin1 = list(dfs_interior_nodes(vertexgraph,
                                         starting=vertexset1,
                                         boundary=boundary,
                                         subset=constrained_set.difference(boundary2)))
        allin2 = list(dfs_interior_nodes(vertexgraph,
                                         starting=vertexset2,
                                         boundary=boundary,
                                         subset=constrained_set.difference(boundary1)))
        
        vertexset1 = set(allin1).union(vertexset1).union(straightened_path)
        vertexset2 = set(allin2).union(vertexset2).union(straightened_path)
        tris1 = []
        tris2 = []
        trisneither = []
        for tri in combined_tris:
            if tri[0] in vertexset1 and tri[1] in vertexset1 and tri[2] in vertexset1:
                tris1.append(tri)
            elif tri[0] in vertexset2 and tri[1] in vertexset2 and tri[2] in vertexset2:
                tris2.append(tri)
            else:
                trisneither.append(tri)
        
        #this can happen if the straightened path cuts off another face's edges
        if len(trisneither) != 0:
            continue
        
        # This can happen if the shortest path actually encompasses
        # the smaller face, but this would be equivalent to merging the
        # two faces. If we didn't merge these two in the previous step,
        # it was because the cost was too high or it would violate one of
        # the constraints, so just ignore this 
        if len(tris1) == 0 or len(tris2) == 0:
            continue
        
        #this can happen if the straightened path cuts off another face's edges
        if len(tris1) + len(tris2) != len(combined_tris):
            continue

        facegraph.add_edge(face1, face2)
        facegraph.add_node(face1, tris=tris1, edges=new_edges1)
        facegraph.add_node(face2, tris=tris2, edges=new_edges2)
        
    end_operation()
    print next(t)
    
    print 'forming initial chart parameterizations...',
    begin_operation()
    for (face, facedata) in facegraph.nodes_iter(data=True):
        border_edges = facedata['edges']
        chart_tris = facedata['tris']
        
        unique_verts = numpy.unique(numpy.array(chart_tris))
        border_verts = numpy.unique(numpy.array(list(border_edges)))
        interior_verts = numpy.setdiff1d(unique_verts, border_verts, assume_unique=True)
        
        #fakegraph = nx.Graph()
        #fakegraph.add_node(0, tris=chart_tris)
        #renderCharts(fakegraph, all_vertices, lineset=[border_edges])
        
        pt2edge = {}
        for edge in border_edges:
            v1, v2 = edge
            v1list = pt2edge.get(v1, [])
            v2list = pt2edge.get(v2, [])
            v1list.append(edge)
            v2list.append(edge)
            pt2edge[v1] = v1list
            pt2edge[v2] = v2list
        
        numvisited = 0
        total_dist = 0
        cycled = False
        start_pt = next(iter(border_edges))[0]
        curpt = start_pt
        boundary_path = []
        while not cycled:
            edge = pt2edge[curpt][0]
            sanedge = edge
            if edge[0] != curpt:
                sanedge = (edge[1], edge[0])
            nextpt = sanedge[1]
            boundary_path.append(sanedge)
            numvisited += 1
            total_dist += v3dist(all_vertices[sanedge[0]], all_vertices[sanedge[1]])
            nextopts = pt2edge[nextpt]
            nextopts.remove(edge)
            if len(nextopts) > 0:
                pt2edge[nextpt] = nextopts
            else:
                del pt2edge[nextpt]
            curpt = nextpt
            if curpt == start_pt:
                cycled = True
        assert(numvisited == len(border_edges))
        
        curangle = 0
        for edge in boundary_path:
            angle = v3dist(all_vertices[edge[0]], all_vertices[edge[1]]) / total_dist
            curangle += angle * 2 * math.pi
            x, y = math.sin(curangle), math.cos(curangle)
            vertexgraph.add_node(edge[0], u=x)
            vertexgraph.add_node(edge[0], v=y)
        
        if len(interior_verts) > 0:
        
            vert2idx = {}
            for i, v in enumerate(interior_verts):
                vert2idx[v] = i
            
            A = numpy.zeros(shape=(len(interior_verts), len(interior_verts)), dtype=numpy.float32)
            Bu = numpy.zeros(len(interior_verts))
            Bv = numpy.zeros(len(interior_verts))
            sumu = numpy.zeros(len(interior_verts))
            
            for edge in vertexgraph.subgraph(unique_verts).edges_iter():
                v1, v2 = edge
                if v1 in border_verts and v2 in border_verts:
                    continue
                
                edgelen = v3dist(all_vertices[v1], all_vertices[v2])
                if v1 in border_verts:
                    Bu[vert2idx[v2]] += edgelen * vertexgraph.node[v1]['u']
                    Bv[vert2idx[v2]] += edgelen * vertexgraph.node[v1]['v']
                    sumu[vert2idx[v2]] += edgelen
                elif v2 in border_verts:
                    Bu[vert2idx[v1]] += edgelen * vertexgraph.node[v2]['u']
                    Bv[vert2idx[v1]] += edgelen * vertexgraph.node[v2]['v']
                    sumu[vert2idx[v1]] += edgelen
                else:
                    A[vert2idx[v1]][vert2idx[v2]] = -1 * edgelen
                    A[vert2idx[v2]][vert2idx[v1]] = -1 * edgelen
                    sumu[vert2idx[v1]] += edgelen
                    sumu[vert2idx[v2]] += edgelen
            
            Bu.shape = (len(Bu), 1)
            Bv.shape = (len(Bv), 1)
            sumu.shape = (len(sumu), 1)
            
            A /= sumu
            Bu /= sumu
            Bv /= sumu
            numpy.fill_diagonal(A, 1)
            
            interior_us = numpy.linalg.solve(A, Bu)
            interior_vs = numpy.linalg.solve(A, Bv)
            for (i, (u, v)) in enumerate(zip(interior_us, interior_vs)):
                vertexgraph.add_node(interior_verts[i], u=u, v=v)
        
        #import Image, ImageDraw
        #W, H = 500, 500
        #im = Image.new("RGB", (W,H), (255,255,255))
        #draw = ImageDraw.Draw(im)
        
        #for edge in vertexgraph.subgraph(unique_verts).edges_iter():
        #    pt1, pt2 = edge
        #    u1 = vertexgraph.node[pt1]['u']
        #    u2 = vertexgraph.node[pt2]['u']
        #    v1 = vertexgraph.node[pt1]['v']
        #    v2 = vertexgraph.node[pt2]['v']
        #    uv1 = ( (u1+1)/2.0 * W, (v1+1)/2.0 * H )
        #    uv2 = ( (u2+1)/2.0 * W, (v2+1)/2.0 * H )
        #    draw.ellipse((uv1[0]-5, uv1[1]-5, uv1[0]+5, uv1[1]+5), outline=(0,0,0), fill=(255,0,0))
        #    draw.ellipse((uv2[0]-5, uv2[1]-5, uv2[0]+5, uv2[1]+5), outline=(0,0,0), fill=(255,0,0))
        #    draw.line([uv1, uv2], fill=(0,0,0))
        
        #del draw
        #im.show()
        
        #import sys
        #sys.exit(0)
        
    end_operation()
    print next(t)
    
    return mesh

def FilterGenerator():
    class SandlerSimplificationFilter(OpFilter):
        def __init__(self):
            super(SandlerSimplificationFilter, self).__init__('sander_simplify', 'Simplifies the mesh based on sandler, et al. method.')
        def apply(self, mesh):
            sandler_simplify(mesh)
            return mesh
    return SandlerSimplificationFilter()
from meshtool.filters import factory
factory.register(FilterGenerator().name, FilterGenerator)