import numpy as np
import networkx as nx
from pysb.bng import generate_equations
import re
import pandas as pd
from math import log10
import sympy
import tropical.util as hf
from tropical.util import parse_name
from collections import defaultdict, OrderedDict
from anytree import Node, findall
from anytree.exporter import DictExporter
from anytree.importer import DictImporter
from collections import ChainMap
import time
try:
    from pathos.multiprocessing import ProcessingPool as Pool
except ImportError:
    Pool = None

try:
    import h5py
except ImportError:
    h5py = None


class DomPath(object):
    """

    Parameters
    ----------
    model: PySB model
        Model to analyze
    tspan: vector-like
        Time of the simulation
    dom_om: float
        Order of magnitude to consider dominancy
    target
    depth
    """
    def __init__(self, model, simulations, dom_om, target, depth):
        self._model = model
        self._trajectories, self._parameters, self._nsims, self._tspan = hf.get_simulations(simulations)
        self._par_name_idx = {j.name: i for i, j in enumerate(self.model.parameters)}
        self._dom_om = dom_om
        self._target = target
        self._depth = depth
        if self._nsims == 1:
            self._parameters = self._parameters[0]
        generate_equations(self.model)  # TODO make sure this is needed

    @property
    def model(self):
        return self._model

    @property
    def trajectories(self):
        return self._trajectories

    @property
    def parameters(self):
        return self._parameters

    @property
    def nsims(self):
        return self._nsims

    @property
    def tspan(self):
        return self._tspan

    @property
    def par_name_idx(self):
        return self._par_name_idx

    @property
    def dom_om(self):
        return self._dom_om

    @property
    def target(self):
        return self._target

    @property
    def depth(self):
        return self._depth

    def create_bipartite_graph(self):
        """
        Creates bipartite graph with species and reaction nodes of the pysb model
        Returns
        -------

        """
        graph = nx.DiGraph(name=self.model.name)
        ic_species = [cp for cp, parameter in self.model.initial_conditions]
        for i, cp in enumerate(self.model.species):
            species_node = 's%d' % i
            slabel = re.sub(r'% ', r'%\\l', str(cp))
            slabel += '\\l'
            color = "#ccffcc"
            # color species with an initial condition differently
            if len([s for s in ic_species if s.is_equivalent_to(cp)]):
                color = "#aaffff"
            graph.add_node(species_node,
                           label=slabel,
                           shape="Mrecord",
                           fillcolor=color, style="filled", color="transparent",
                           fontsize="12",
                           margin="0.06,0")
        for i, reaction in enumerate(self.model.reactions_bidirectional):
            reaction_node = 'r%d' % i
            graph.add_node(reaction_node,
                           label=reaction_node,
                           shape="circle",
                           fillcolor="lightgray", style="filled", color="transparent",
                           fontsize="12",
                           width=".3", height=".3", margin="0.06,0")
            reactants = set(reaction['reactants'])
            products = set(reaction['products'])
            modifiers = reactants & products
            reactants = reactants - modifiers
            products = products - modifiers
            attr_reversible = {'dir': 'both', 'arrowtail': 'empty'} if reaction['reversible'] else {}
            for s in reactants:
                self.r_link(graph, s, i, **attr_reversible)
            for s in products:
                self.r_link(graph, s, i, _flip=True, **attr_reversible)
            for s in modifiers:
                self.r_link(graph, s, i, arrowhead="odiamond")
        return graph

    @staticmethod
    def r_link(graph, s, r, **attrs):
        nodes = ('s%d' % s, 'r%d' % r)
        if attrs.get('_flip'):
            del attrs['_flip']
            nodes = reversed(nodes)
        attrs.setdefault('arrowhead', 'normal')
        graph.add_edge(*nodes, **attrs)

    def get_reaction_flux_df(self, trajectories, parameters):
        """
        Creates a data frame with the reaction rates values at each time point
        Parameters
        ----------
        trajectories: vector-like
            Species trajectories used to calculate the reaction rates
        parameters: vector-like
            Model parameters. Parameters must have the same order as the model

        Returns
        -------

        """
        param_values = parameters
        rxns_names = ['r{0}'.format(rxn) for rxn in range(len(self.model.reactions_bidirectional))]
        rxns_df = pd.DataFrame(columns=self.tspan, index=rxns_names)
        param_dict = dict((p.name, param_values[i]) for i, p in enumerate(self.model.parameters))

        for idx, reac in enumerate(self.model.reactions_bidirectional):
            rate_reac = reac['rate']
            # Getting species and parameters from the reaction rate
            variables = [atom for atom in rate_reac.atoms(sympy.Symbol)]
            args = [0] * len(variables)  # arguments to put in the lambdify function
            for idx2, va in enumerate(variables):
                # Getting species index
                if str(va).startswith('__'):
                    sp_idx = int(''.join(filter(str.isdigit, str(va))))
                    args[idx2] = trajectories[:, sp_idx]
                else:
                    args[idx2] = param_dict[va.name]
            func = sympy.lambdify(variables, rate_reac, modules=dict(sqrt=np.lib.scimath.sqrt))
            react_rate = func(*args)
            rxns_df.loc['r{0}'.format(idx)] = react_rate
        rxns_df['Total'] = rxns_df.sum(axis=1)
        return rxns_df

    @staticmethod
    def get_reaction_incoming_species(network, r):
        """
        Gets all the edges that are coming from species nodes and are going to a reaction node r
        Parameters
        ----------
        network: nx.Digraph
            Networkx directed network
        r: str
            Node name

        Returns
        -------
        Species that are involved in reaction r
        """
        in_edges = network.in_edges(r)
        sp_nodes = [n[0] for n in in_edges]
        # Sort the incoming nodes to get the same results in each simulation
        return natural_sort(sp_nodes)

    def dominant_paths(self, trajectories, parameters):
        """
        Traceback a dominant path from a defined target
        Parameters
        ----------
        target : str
            Node label from network, Node from which the pathway starts
        depth : int
            The depth of the pathway

        Returns
        -------

        """
        network = self.create_bipartite_graph()
        reaction_flux_df = self.get_reaction_flux_df(trajectories, parameters)

        path_rlabels = {}
        # path_sp_labels = {}
        signature = [0] * len(self.tspan[1:])
        prev_neg_rr = []
        # First we iterate over time points
        for label, t in enumerate(self.tspan[1:]):
            # Get reaction rates that are negative to see which edges have to be reversed
            neg_rr = reaction_flux_df.index[reaction_flux_df[t] < 0].tolist()
            if not neg_rr or prev_neg_rr == neg_rr:
                pass
            else:
                # Compare the negative indices from the current iteration to the previous one
                # and flip the edges of the ones that have changed
                rr_changes = list(set(neg_rr).symmetric_difference(set(prev_neg_rr)))

                for r_node in rr_changes:
                    # remove in and out edges of the node to add them in the reversed direction
                    in_edges = network.in_edges(r_node)
                    out_edges = network.out_edges(r_node)
                    edges_to_remove = list(in_edges) + list(out_edges)
                    network.remove_edges_from(edges_to_remove)
                    edges_to_add = [edge[::-1] for edge in edges_to_remove]
                    network.add_edges_from(edges_to_add)

                prev_neg_rr = neg_rr

            dom_nodes = {self.target: [[self.target]]}
            all_rdom_nodes = [0] * self.depth
            t_paths = [0] * self.depth
            # Iterate over the depth
            for d in range(self.depth):
                all_dom_nodes = OrderedDict()
                dom_r3 = []
                for node_doms in dom_nodes.values():
                    # Looping over dominant nodes i.e [[s1, s2], [s4, s8]]
                    dom_r2 = []
                    for nodes in node_doms:
                        # Looping over one of the dominants i.e. [s1, s2]
                        dom_r1 = []
                        for node in nodes:
                            # node = s1
                            # Obtaining the incoming edges of the species node
                            in_edges = network.in_edges(node)
                            if not in_edges:
                                continue
                            # Obtaining the reaction rate value of the rate node that connects to
                            # the species node
                            fluxes_in = {edge: log10(reaction_flux_df.loc[edge[0], t])
                                         for edge in in_edges if reaction_flux_df.loc[edge[0], t] > 0}
                            if not fluxes_in:
                                continue

                            max_val = np.amax(list(fluxes_in.values()))
                            # Obtaining dominant species and reactions nodes
                            dom_r_nodes = [n[0] for n, i in fluxes_in.items() if i > (max_val - self.dom_om)]
                            # Sort the dominant r nodes to get the same results in each simulation
                            dom_r_nodes = natural_sort(dom_r_nodes)
                            dom_sp_nodes = [self.get_reaction_incoming_species(network, reaction_nodes)
                                            for reaction_nodes in dom_r_nodes]
                            # Get the species nodes from the reaction nodes to keep back tracking the pathway
                            all_dom_nodes[node] = dom_sp_nodes
                            # all_rdom_nodes.append(dom_r_nodes)
                            dom_r1.append(sorted(dom_r_nodes))

                        dom_nodes = all_dom_nodes
                        dom_r2.append(dom_r1)
                    dom_r3.append(dom_r2)
                all_rdom_nodes[d] = dom_r3
                t_paths[d] = dom_nodes

            all_rdom_noodes_str = str(all_rdom_nodes)
            # sp_paths = []
            # This is to create a tree with the information of the dominant species
            root = Node(self.target, order=0)
            for idx, ds in enumerate(t_paths):
                for pa, v in ds.items():
                    sps = np.concatenate(v)
                    for sp in sps:
                        p = findall(root, filter_=lambda n: n.name == pa and n.order == idx)
                        for m in p:
                            Node(sp, parent=m, order=idx+1)
                        # sp_paths.append((sp, idx+1))
            # sp_paths.insert(0, (self.target, 0))

            rdom_label = list_to_int(find_numbers(all_rdom_noodes_str))
            path_rlabels[rdom_label] = DictExporter().export(root)
            signature[label] = rdom_label
            # path_sp_labels[rdom_label] = t_paths
        return signature, path_rlabels

    def get_path_signatures(self, cpu_cores=1, verbose=False):
        if cpu_cores == 1 or self.nsims == 1:
            if self.nsims == 1:
                signatures, labels = self.dominant_paths(self.trajectories, self.parameters)
                signatures_labels = {'signatures': signatures, 'labels': labels}
                return signatures_labels
            else:
                all_signatures = [0] * self.nsims
                all_labels = [0] * self.nsims
                for idx in range(self.nsims):
                    all_signatures[idx], all_labels[idx] = self.dominant_paths(self.trajectories[idx], self.parameters[idx])
                all_labels = dict(ChainMap(*all_labels))
                all_signatures = np.array(all_signatures)
                signatures_labels = {'signatures': all_signatures, 'labels': all_labels}
                return signatures_labels
        else:
            if Pool is None:
                raise Exception('Please install the pathos package for this feature')
        # if self.nsims == 1:
        #     self.trajectories = [self.trajectories]
        #     self.parameters = [self.parameters]

            p = Pool(cpu_cores)
            res = p.amap(self.dominant_paths, self.trajectories, self.parameters)
            if verbose:
                while not res.ready():
                    print('We\'re not done yet, %s tasks to go!' % res._number_left)
                    time.sleep(60)
            signatures_labels = res.get()
            signatures = [0] * len(signatures_labels)
            labels = [0] * len(signatures_labels)
            for idx, sl in enumerate(signatures_labels):
                signatures[idx] = sl[0]
                labels[idx] = sl[1]
            all_labels = dict(ChainMap(*labels))
            signatures = np.array(signatures)
            unique_signatures = np.unique(signatures)
            new_labels = {va: i for i, va in enumerate(unique_signatures)}
            new_paths = {new_labels[key]: value for key, value in all_labels.items()}
            del all_labels
            signatures_df = signatures_to_dataframe(signatures, self.tspan)
            def reencode(x):
                return new_labels[x]
            signatures_df = signatures_df.applymap(reencode)
            # signatures_labels = {'signatures': signatures, 'labels': all_labels}
            return signatures_df, new_paths


def signatures_to_dataframe(signatures, tspan):
    def time_values(t):
        # We add 1 because the first time point is ignored because there could
        # be equilibration issues
        return tspan[t+1]

    if not isinstance(signatures, np.ndarray):
        signatures = np.concatenate(signatures)
    s = pd.DataFrame(signatures)
    s.rename(time_values, axis='columns', inplace=True)
    return s


def find_numbers(dom_r_str):
    n = map(int, re.findall('\d+', dom_r_str))
    return n


def list_to_int(nums):
        return int(''.join(map(str, nums)))


def merge_dicts(dicts):
    super_dict = defaultdict(set)
    for d in dicts:
        for k, v in d.items():  # use d.iteritems() in python 2
            super_dict[k].add(v)
    return super_dict


def natural_sort(l):
    convert = lambda text: int(text) if text.isdigit() else text.lower()
    alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return sorted(l, key=alphanum_key)

def get_path_descendants(path):
    """ Get the set of descendants for a tree like path dict.
    """

    importer = DictImporter()
    root = importer.import_(path)
    descendants = set([descendant_node.name for descendant_node in root.descendants])
    return descendants

def global_conserved_species_analysis(paths, path_signatures, model):
    """
    Computes the fraction of dominant paths a species is in. It is taken over
        all simulations and all timepoints.
    Parameters
    ----------
    paths: dict
        Nested tree structure dict of paths as returned from
            DomPath.get_path_signatures()
    path_signatures: pandas.DataFrame
        The dominant path signatures for each simulation (across all
            timepoints).
    model: pysb.Model
        The model that is being used.

    Returns
    -------
    A list of tuples with the species codename
        (i.e. 's' + str( model.species_index)) and the fraction of dominant
        paths that species was in.
    """

    def convert_names(list_o_tuple):
        new_list_o_tuple = []
        for i, item in enumerate(list_o_tuple):
            sname = item[0]
            node_idx = list(find_numbers(sname))[0]
            node_sp = model.species[node_idx]
            node_name = parse_name(node_sp)
            new_list_o_tuple.append((node_name, item[1]))

        return new_list_o_tuple
    generate_equations(model)
    species_all = model.species
    #print(species_all)
    n_species_all = len(species_all)
    spec_dict = dict()
    spec_counts = np.array([0.0] * n_species_all)
    #species_all_snames = []
    for i, species in enumerate(species_all):
        sname = "s{}".format(i)
        spec_dict[sname] = {'name': species, 'index': i}

    path_species = dict()
    for i, key in enumerate(paths.keys()):
        path = paths[key]
        descendants = get_path_descendants(path)
        #print(descendants)
        path_species[i] = descendants
    path_signatures_np = path_signatures.values
    n_sims = path_signatures_np.shape[0]
    n_tp = path_signatures_np.shape[1]
    #print(n_sims, n_tp)
    #quit()
    n_tot = 0.0
    for i in range(n_sims):
        for j in range(n_tp):
            n_tot += 1.0
            dom_path_id = path_signatures_np[i][j]
            #print(dom_path_id)
            for descendant in path_species[dom_path_id]:
            #    print(descendant)
                d_id = spec_dict[descendant]['index']
                spec_counts[d_id] += 1.0
    #print(n_tot)
    spec_fracs = spec_counts / n_tot
    #quit()
    spec_frac_dict = dict()
    for spec in spec_dict.keys():
        spec_frac_dict[spec] = spec_fracs[spec_dict[spec]['index']]
    sorted_by_value = sorted(spec_frac_dict.items(), key=lambda kv: -kv[1])

    return convert_names(sorted_by_value)
