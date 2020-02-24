import os
from collections import Iterable, OrderedDict
from concurrent.futures import ProcessPoolExecutor
from pysb.simulator.scipyode import SerialExecutor
import pandas as pd
import numpy as np
import editdistance
from matplotlib.colors import ListedColormap, BoundaryNorm
from scipy.spatial.distance import pdist, squareform
from sklearn import metrics
import sklearn.cluster as cluster
import pydyno.lcs as lcs
from pydyno.distinct_colors import distinct_colors
from pydyno.kmedoids import kMedoids
from pydyno.plot_sequences import PlotSequences

try:
    import hdbscan
except ImportError:
    hdbscan = None

try:
    import h5py
except ImportError:
    h5py = None

# Valid metrics from scikit-learn
_VALID_METRICS = [
    'braycurtis',
    'canberra',
    'chebyshev',
    'cityblock',
    'correlation',
    'cosine',
    'dice',
    'euclidean',
    'hamming',
    'jaccard',
    'kulsinski',
    'mahalanobis',
    'minkowski',
    'rogerstanimoto',
    'russellrao',
    'seuclidean',
    'sokalmichener',
    'sokalsneath',
    'sqeuclidean',
    'wminkowski',
    'yule'
    ]

_VALID_CLUSTERING = ['hdbscan', 'kmedoids', 'agglomerative', 'spectral']


def lcs_dist_same_length(seq1, seq2):
    """
    Longest common subsequence metric as defined by CESS H. ELZINGA in
    'Sequence analysis: metric representations of categorical time series'

    Parameters
    ----------
    seq1 : array-like
        Sequence 1
    seq2 : array-like
        Sequence 2

    Returns
    -------

    """
    seq_len = len(seq1)
    d_1_2 = 2 * seq_len - 2 * lcs.lcs_std(seq1, seq2)[0]
    return d_1_2


def lcs_dist_diff_length(seq1, seq2):
    seq1_len = len(seq1)
    seq2_len = len(seq2)

    d_1_2 = seq1_len + seq2_len - 2 * lcs.lcs_std(seq1, seq2)[0]
    return d_1_2


def levenshtein(seq1, seq2):
    d_1_2 = editdistance.eval(seq1, seq2).__float__()
    return d_1_2


class SeqAnalysis:
    """
    Class to do analysis and visualizations of discretized trajectories
    Parameters
    ----------
    sequences: str, pd.DataFrame, np.ndarray, list
        Sequence data from the discretization of a PySB model. If str it must be a csv file
        with the sequences as rows and the first row must have the time points of the simulation.
    target: str
        Species target. It has to be in a format `s1` where the number
        represents the species index
    """
    def __init__(self, sequences, target):
        # Checking seqdata
        if isinstance(sequences, str):
            if os.path.isfile(sequences):
                data_seqs = pd.read_csv(sequences, header=0, index_col=0)
                # convert column names into float numbers
                data_seqs.columns = [float(i) for i in data_seqs.columns.tolist()]
            else:
                raise TypeError('String is not a file')
        elif isinstance(sequences, Iterable):
            data_seqs = pd.DataFrame(data=sequences)
        elif isinstance(sequences, pd.DataFrame):
            data_seqs = sequences
        else:
            raise TypeError('data type not valid')

        # Rename index if seq_idx doesn't exist in the dataframe
        if 'seq_idx' not in data_seqs.index.names:
            data_seqs.index.rename('seq_idx', inplace=True)
        # Making sure that we have a count name in the data frame
        if 'count' not in data_seqs.index.names:
            data_seqs['count'] = 1
            data_seqs.set_index(['count'], append=True, inplace=True)

        self._sequences = data_seqs
        self._target = target

        # Obtaining unique states in the sequences
        unique_states = pd.unique(data_seqs[data_seqs.columns.tolist()].values.ravel())
        unique_states.sort()
        self._unique_states = unique_states

        # Assigning a color to each unique state
        n_unique_states = len(self._unique_states)
        if n_unique_states <= 1022:
            colors = distinct_colors(len(self._unique_states))
        else:
            n_iterations = int(np.floor(n_unique_states/1022))
            colors = distinct_colors(1022)
            remaining_states = n_unique_states - 1022
            for _ in range(n_iterations):
                if remaining_states > 1022:
                    colors += distinct_colors(1022)
                    remaining_states -= 1022
                else:
                    colors += distinct_colors(remaining_states)

        self._states_colors = OrderedDict((state, colors[x]) for x, state, in enumerate(self._unique_states))
        self.cmap, self.norm = self.cmap_norm()

        # Dissimilarity matrix, cluster labels and cluster method
        self._diss = None
        self._labels = None
        self._cluster_method = None

    def __repr__(self):
        """
        Return a string representation of the sequences.
        """
        return str(self._sequences)

    def cmap_norm(self):
        cmap = ListedColormap(list(self._states_colors.values()))
        bounds = list(self._states_colors)
        bounds.append(bounds[-1] + 1)
        norm = BoundaryNorm(bounds, cmap.N)
        return cmap, norm

    def unique_sequences(self):
        """
        Obtain the unique sequence in the dataframe of sequences. This adds a new
        column with the count of the repeated sequences

        Returns
        -------
        pd.DataFrame with the unique sequences

        """
        data_seqs = self._sequences.groupby(self._sequences.columns.tolist(),
                                            sort=False).size().rename('count').reset_index()
        data_seqs.index.rename('seq_idx', inplace=True)
        data_seqs.set_index(['count'], append=True, inplace=True)
        # data_seqs.set_index([list(range(len(data_seqs))), 'count'], inplace=True)
        return SeqAnalysis(data_seqs, self.target)

    def truncate_sequences(self, idx):
        """
        Truncates the sequences at the index passed
        Parameters
        ----------
        idx: int
            Index at which the sequences will be truncated

        Returns
        -------
        Sequences truncated at the idx indicated
        """
        data_seqs = self._sequences[self._sequences.columns.tolist()[:idx]]
        return SeqAnalysis(data_seqs, self.target)

    def dissimilarity_matrix(self, metric='LCS'):
        """
        Get dissimilarity matrix using the passed metric
        Parameters
        ----------
        metric: str
            One of the metrics defined in _VALID_METRICS
        n_jobs: int
            Number of processors used to calculate the dissimilarity matrix

        Returns
        -------
        dissimilarity matrix: np.ndarray
        """
        # Sort sequences
        sorted_seqs = self._sequences.sort_values(by=self._sequences.columns.tolist(), inplace=False)
        old_sorted_idxs = np.argsort(sorted_seqs.index)
        # Get unique sequences based on the sorted dataframe. This allows us to connect
        # the sorted dataframe with the unique sequences dataframes to relate them back
        # after obtaining the dissimilarity matrix
        unique_sequences = sorted_seqs.groupby(sorted_seqs.columns.tolist(),
                                               sort=False).size().rename('count').reset_index()
        unique_sequences.set_index([list(range(len(unique_sequences))), 'count'], inplace=True)

        if metric in _VALID_METRICS:
            diss = squareform(pdist(unique_sequences.values, metric=metric))
        elif metric == 'LCS':
            diss = squareform(pdist(unique_sequences.values, metric=lcs_dist_same_length))
        elif metric == 'levenshtein':
            diss = squareform(pdist(unique_sequences.values, metric=levenshtein))
        elif callable(metric):
            diss = squareform(pdist(unique_sequences.values, metric=metric))
        else:
            raise ValueError('metric not supported')

        count_seqs = unique_sequences.index.get_level_values(1).values
        seq_idxs = unique_sequences.index.get_level_values(0).values
        repeat_idxs = np.repeat(seq_idxs, count_seqs)
        diss = diss[repeat_idxs]
        # This is to be able to math cluster idxs with the parameter idxs
        diss = diss[old_sorted_idxs]
        diss = diss.T
        diss = diss[repeat_idxs]
        diss = diss[old_sorted_idxs]
        self._diss = diss
        return diss

    def select_seqs_group(self, group):
        """
        Select a group of sequences for analysis

        Parameters
        ----------
        group: list-like or int
            if int it must be one of the cluster labels, if list-like it must be a list of
            sequence indices to select

        Returns
        -------
        seqAnalysis
            a new object with the selected sequences
        """
        if isinstance(group, int):
            if self.labels is None:
                raise Exception('Cluster the sequences first')
            idxs = np.where(self.labels == group)[0]
        else:
            if isinstance(group, list):
                idxs = np.array(group)
            elif isinstance(group, np.ndarray):
                idxs = group
            else:
                raise TypeError('group must be a list or np.ndarray object')

        new_diss = self.diss[idxs[:, None], idxs[None, :]]
        new_seqs = self.sequences.iloc[idxs]
        new_seq_analysis = SeqAnalysis(new_seqs, self.target)
        new_seq_analysis.diss = new_diss
        return new_seq_analysis

    @property
    def sequences(self):
        return self._sequences

    @property
    def target(self):
        return self._target

    @property
    def diss(self):
        if self._diss is None:
            print('dissimilarity_matrix function must be ran beforehand '
                  'to obtain the values.')
        return self._diss

    @diss.setter
    def diss(self, value):
        if isinstance(value, np.ndarray):
            n_rows = len(self.sequences.index)
            if value.shape == (n_rows, n_rows):
                self._diss = value
            else:
                raise ValueError("new dissimilarity matrix must be a square matrix of order "
                                 "equal to the length of the number of sequences")
        elif value is None:
            self._diss = None
        else:
            raise TypeError('dissimilarity matrix must be a numpy ndarray')

    @property
    def labels(self):
        if self._labels is None:
            print('A clustering function must be run beforehand '
                  'to obtain the label values')
        return self._labels

    @labels.setter
    def labels(self, value):
        if isinstance(value, Iterable):
            if len(value) != self.sequences.shape[1]:
                self._labels = value
            else:
                raise ValueError('The new cluster labels must be the same length as '
                                 'the number of sequences')
        elif value is None:
            self._labels = None
        else:
            raise TypeError('Labels must be a vector with the labels from clustering')

    @property
    def cluster_method(self):
        if self._cluster_method is None:
            print('A clustering function must be run beforehand'
                  'to save the clustering method name')
        return self._cluster_method

    @cluster_method.setter
    def cluster_method(self, value):
        if value in _VALID_CLUSTERING + [None]:
            self._cluster_method = value
        else:
            raise ValueError('Clustering method not valid')

    @property
    def unique_states(self):
        return self._unique_states

    @property
    def states_colors(self):
        return self._states_colors

    _assign = lambda d, k: lambda f: d.setdefault(k, f)
    representativeness = {}

    @_assign(representativeness, 'neighborhood')
    def neighborhood_density(self, proportion, sequences_idx=None):
        """
        Representativeness using neighborhood density method
        Parameters
        ----------
        proportion : float
            Proportion of the maximal distance between sequences for a sequence to be considered
            within a neighborhood
        sequences_idx : list-like
            Indices of the sequences used to calculate the neighborhood density

        Returns
        -------
        np.ndarray
            Representative sequence

        """
        seq_len = self._sequences.shape[1]
        ci = 1  # ci is the indel cost
        s = 2  # s is the substitution cost
        # this is the maximal distance between two sequences using the optimal matching metric
        # with indel cost ci=1 and substitution cost s=2. Gabardinho et al (2011) communications in computer
        # and information science
        theo_max_dist = seq_len * min([2 * ci, s])
        neighbourhood_radius = theo_max_dist * proportion

        def density(seq_dists):
            seq_density = len(seq_dists[seq_dists < neighbourhood_radius])
            return seq_density

        if sequences_idx is not None:
            seqs = self._sequences.iloc[sequences_idx]
            seqs_diss = self._diss[sequences_idx][:, sequences_idx]
        else:
            seqs = self._sequences
            seqs_diss = self._diss
        seqs_neighbours = np.apply_along_axis(density, 1, seqs_diss)
        decreasing_seqs = seqs.iloc[seqs_neighbours.argsort()[::-1]]
        return decreasing_seqs.iloc[0].values

    @_assign(representativeness, 'centrality')
    def centrality(self, sequences_idx=None):
        """
        Representativeness using the centrality method
        Parameters
        ----------
        sequences_idx : list-like
            Indices of the sequences used to calculate the neighborhood density

        Returns
        -------
        np.ndarray
            Representative sequence
        """
        if sequences_idx is not None:
            seqs = self._sequences.iloc[sequences_idx]
            seqs_diss = self._diss[sequences_idx][:, sequences_idx]
        else:
            seqs = self._sequences
            seqs_diss = self._diss
        seqs_centrality_idx = seqs_diss.sum(axis=0).argsort()
        decreasing_seqs = seqs.iloc[seqs_centrality_idx]
        return decreasing_seqs.iloc[0].name, decreasing_seqs.iloc[0].values

    @_assign(representativeness, 'frequency')
    def frequency(self, sequences_idx=None):
        """
        Representativeness using the frequency method
        Parameters
        ----------
        sequences_idx : list-like
            Indices of the sequences used to calculate the neighborhood density

        Returns
        -------
        np.ndarray
            Representative sequence
        """
        decreasing_seqs = self.neighborhood_density(proportion=1, sequences_idx=sequences_idx)
        return decreasing_seqs

    def _dispatch(self, k):  # , *args, **kwargs):
        try:
            method = self.representativeness[k].__get__(self, type(self))
        except KeyError:
            assert k in self.representativeness, "invalid operation: " + repr(k)
        return method  # (*args, **kwargs)

    def seq_representativeness(self, method='frequency', clus_labels=None, **kwargs):
        """

        Parameters
        ----------
        method
        clus_labels: vector-like
            Labels must be in the same order of seqdata
        kwargs

        Returns
        -------

        """
        rep_method = self._dispatch(method)
        if clus_labels is not None:
            clusters = set(clus_labels)
            clus_rep = {}
            for clus in clusters:
                clus_seqs = self._sequences.iloc[clus_labels == clus]
                clus_idxs = clus_seqs.index.get_level_values(0).values
                rep = rep_method(sequences_idx=clus_idxs, **kwargs)
                clus_rep[clus] = rep
            return clus_rep
        else:
            rep = rep_method(**kwargs)
            return rep

    def plot_sequences(self, type_fig='modal', plot_all=False, title='', dir_path='', sort_seq=None):
        """
        Function to plot three different figures of the sequences.
        The modal figure takes the mode state at each time and plots
        the percentage of that state compated to all the other states.

        The trajectories figure plots each of the sequences.

        The entropy figure plots the entropy calculated from the
        percentages of each of the states at each time relative
        to the total.

        Parameters
        ----------
        type_fig: str
            Type of figure to plot. Valid values are: `modal`, `trajectories`, `entropy`
        plot_all: bool
            If true the function plots all the sequences, otherwise it plots the clustered
            sequences in different subplots
        title: str
            Title of the figure
        dir_path: str
            Path to directory where plots are going to be saved
        sort_seq: str
            Method to sort sequences for a plot. Valid values are: `silhouette`.
             It is only available when the type of plot is `trajectories`

        Returns
        -------

        """
        ps = PlotSequences(self)
        ps.plot_sequences(type_fig=type_fig, plot_all=plot_all, title=title, dir_path=dir_path, sort_seq=sort_seq)

    # Clustering

    def hdbscan_clustering(self, min_cluster_size=50, min_samples=5,
                           alpha=1.0, cluster_selection_method='eom', **kwargs):
        """

        Parameters
        ----------
        min_cluster_size
        min_samples
        alpha
        cluster_selection_method
        kwargs

        Returns
        -------

        """
        if hdbscan is None:
            raise Exception('Please install the hdbscan package for this feature')
        if self.diss is None:
            raise Exception('Get the dissimilarity matrix first')
        hdb = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size, min_samples=min_samples,
                              alpha=alpha, cluster_selection_method=cluster_selection_method,
                              metric='precomputed', **kwargs).fit(self.diss)
        self._labels = hdb.labels_

        self._cluster_method = 'hdbscan'
        return

    def Kmedoids(self, n_clusters):
        """

        Parameters
        ----------
        n_clusters : int
            Number of clusters

        Returns
        -------

        """
        if self.diss is None:
            raise Exception('Get the dissimilarity matrix first')
        kmedoids = kMedoids(self.diss, n_clusters)
        labels = np.empty(len(self.sequences), dtype=np.int32)
        for lb, seq_idx in kmedoids[1].items():
            labels[seq_idx] = lb
        self._cluster_method = 'kmedoids'
        self._labels = labels
        return

    def agglomerative_clustering(self, n_clusters, linkage='average', **kwargs):
        ac = cluster.AgglomerativeClustering(n_clusters=n_clusters, affinity='precomputed',
                                             linkage=linkage, **kwargs).fit(self.diss)
        self._labels = ac.labels_
        self._cluster_method = 'agglomerative'
        return

    def spectral_clustering(self, n_clusters, random_state=None, n_jobs=1, **kwargs):
        gamma = 1. / len(self.diss[0])
        kernel = np.exp(-self.diss * gamma)
        sc = cluster.SpectralClustering(n_clusters=n_clusters, random_state=random_state,
                                        affinity='precomputed', n_jobs=n_jobs, **kwargs).fit(kernel)
        self._labels = sc.labels_
        self._cluster_method = 'spectral'

    def silhouette_score(self):
        """

        Returns : Silhouette score to measure quality of the clustering
        -------

        """
        if self._labels is None:
            raise Exception('you must cluster the signatures first')
        if self._cluster_method == 'hdbscan':
            # Keep only clustered sequences
            clustered = np.where(self._labels != -1)[0]
            updated_labels = self._labels[clustered]
            updated_diss = self.diss[clustered][:, clustered]
            score = metrics.silhouette_score(updated_diss, updated_labels, metric='precomputed')
            return score
        else:
            score = metrics.silhouette_score(self.diss, self._labels, metric='precomputed')
            return score

    def silhouette_score_spectral_range(self, cluster_range, n_jobs=1, random_state=None, **kwargs):
        if isinstance(cluster_range, int):
            cluster_range = list(range(2, cluster_range + 1))  # +1 to cluster up to cluster_range
        elif hasattr(cluster_range, "__len__") and not isinstance(cluster_range, str):
            pass
        else:
            raise TypeError('Type not valid')

        gamma = 1. / len(self.diss[0])
        kernel = np.exp(-self.diss * gamma)
        cluster_silhouette = []
        for num_clusters in cluster_range:
            clusters = cluster.SpectralClustering(num_clusters, n_jobs=n_jobs, affinity='precomputed',
                                                  random_state=random_state, **kwargs).fit(kernel)
            score = metrics.silhouette_score(self.diss, clusters.labels_, metric='precomputed')
            cluster_silhouette.append(score)
        clusters_df = pd.DataFrame({'num_clusters': cluster_range, 'cluster_silhouette': cluster_silhouette})
        return clusters_df

    def silhouette_score_agglomerative_range(self, cluster_range, linkage='average',
                                             num_processors=1, **kwargs):
        """

        Parameters
        ----------
        cluster_range : list-like or int
            Range of the number of clusterings to obtain the silhouette score
        linkage : str
            Type of agglomerative linkage
        num_processors : int
            Number of cores to use
        kwargs : key arguments to pass to the aggomerative clustering function

        Returns
        -------

        """

        if isinstance(cluster_range, int):
            cluster_range = list(range(2, cluster_range + 1))  # +1 to cluster up to cluster_range
        elif hasattr(cluster_range, "__len__") and not isinstance(cluster_range, str):
            pass
        else:
            raise TypeError('Type not valid')
        results = []
        with SerialExecutor() if num_processors == 1 else \
                ProcessPoolExecutor(max_workers=num_processors) as executor:
            for num_clusters in cluster_range:
                results.append(executor.submit(
                    _agg_cluster_score,
                    self.diss,
                    num_clusters,
                    linkage,
                    **kwargs
                ))
            cluster_silhouette = [r.result() for r in results]
        clusters_df = pd.DataFrame({'num_clusters': cluster_range, 'cluster_silhouette': cluster_silhouette})
        return clusters_df

    def calinski_harabaz_score(self):
        if self._labels is None:
            raise Exception('you must cluster the signatures first')
        score = metrics.calinski_harabaz_score(self.sequences, self._labels)
        return score

    def save(self, filename):
        """
        Save a Sequence object to a HDF5 format file
        Parameters
        ----------
        filename: str
            Filename to which the data will be saved

        """
        if h5py is None:
            raise Exception('Please install the h5py package for this feature')

        group_name = 'discretization_result'

        with h5py.File(filename, 'w-') as hdf:
            grp = hdf.create_group(group_name)
            grp.create_dataset('sequences', data=self.sequences.to_records(),
                               compression='gzip', shuffle=True)
            if self._diss is not None:
                grp.create_dataset('dissimilarity_matrix', data=self._diss,
                                   compression='gzip', shuffle=True)
            if self._labels is not None:
                dset = grp.create_group('clustering_information')
                dset.create_dataset('cluster_labels', data=self._labels,
                                    compression='gzip', shuffle=True)
                dset.attrs['cluster_method'] = self._cluster_method

    @classmethod
    def load(cls, filename):
        """
        Load a sequence result from a HDF5 format file.
        Parameters
        ----------
        filename: str
            Filename from which to load data

        Returns
        -------
        SeqAnalysis
            Sequences obtained from doing a discretization analysis
        """
        if h5py is None:
            raise Exception('Please "pip install h5py" for this feature')

        with h5py.File(filename, 'r') as hdf:
            grp = hdf['discretization_result']
            seqs = grp['sequences'][:]

            dm = None
            cluster_method = None
            labels = None

            if 'dissimilarity_matrix' in grp.keys():
                dm = grp['dissimilarity_matrix'][:]

            if 'clustering_information' in grp.keys():
                cluster_dset = grp['clustering_information']
                cluster_method = cluster_dset.attrs['cluster_method']
                labels = cluster_dset['cluster_labels'][:]

            seqRes = cls(sequences=pd.DataFrame.from_records(seqs, index=['seq_idx', 'count']))
            seqRes.cluster_method = cluster_method
            seqRes.labels = labels
            seqRes.diss = dm

        return seqRes


def _agg_cluster_score(diss, num_clusters, linkage='average', **kwargs):
    clusters = cluster.AgglomerativeClustering(num_clusters, linkage=linkage,
                                               affinity='precomputed', **kwargs).fit(diss)
    score = metrics.silhouette_score(diss, clusters.labels_, metric='precomputed')
    return score

    #TODO: Develop gap statistics score for sequences. Maybe get elbow plot as well

    # def transition_rate_matrix(self, time_varying=False, lag=1):
    #     # this code comes from seqtrate from the TraMineR package in r
    #     nbetat = len(self.unique_states)
    #     sdur = self.sequences.shape[1]
    #     alltransitions = np.arange(0, sdur - lag)
    #     numtransition = len(alltransitions)
    #     row_index = pd.MultiIndex.from_product([alltransitions, self.unique_states],
    #                                            names=['time_idx', 'from_state'])  # , names=row_names)
    #     col_index = pd.MultiIndex.from_product([self.unique_states], names=['to_state'])  # , names=column_names)
    #     if time_varying:
    #         array_zeros = np.zeros(shape=(nbetat * numtransition, nbetat))
    #         tmat = pd.DataFrame(array_zeros, index=row_index, columns=col_index)
    #         for sl in alltransitions:
    #             for x in self.unique_states:
    #                 colxcond = self.sequences[[sl]] == x
    #                 PA = colxcond.sum().values[0]
    #                 if PA == 0:
    #                     tmat.loc[sl, x] = 0
    #                 else:
    #                     for y in self.unique_states:
    #                         PAB_p = np.logical_and(colxcond, self.sequences[[sl + lag]] == y)
    #                         PAB = PAB_p.sum().values[0]
    #                         tmat.loc[sl, x][[y]] = PAB / PA
    #     else:
    #         tmat = pd.DataFrame(index=self.unique_states, columns=self.unique_states)
    #         for x in self.unique_states:
    #             # PA = 0
    #             colxcond = self.sequences[alltransitions] == x
    #             if numtransition > 1:
    #                 PA = colxcond.sum(axis=1).sum()
    #             else:
    #                 PA = colxcond.sum()
    #             if PA == 0:
    #                 tmat.loc[x] = 0
    #             else:
    #                 for y in self.unique_states:
    #                     if numtransition > 1:
    #                         PAB_p = np.logical_and(colxcond, self.sequences[alltransitions + lag] == y)
    #                         PAB = PAB_p.sum(axis=1).sum()
    #                     else:
    #                         PAB_p = np.logical_and(colxcond, self.sequences[alltransitions + lag] == y)
    #                         PAB = PAB_p.sum()
    #                     tmat.loc[x][[y]] = PAB / PA
    #
    #     return tmat

    # def seqlogp(self, prob='trate', time_varying=True, begin='freq'):
    #     sl = self.sequences.shape[1]  # all sequences have the same length for our analysis
    #     maxage = sl
    #     nbtrans = maxage - 1
    #     agedtr = np.zeros(maxage)

    # def cluster_percentage_color(self, representative_method='centrality', **kwargs):
    #     if self.labels is None:
    #         raise Exception('you must cluster the signatures first')
    #
    #     rep_method = self.dispatch(representative_method)
    #     clusters = set(self.labels)
    #     colors = distinct_colors(len(clusters))
    #     cluster_inf = {}
    #     for clus in clusters:
    #         clus_seqs = self.sequences.iloc[self.labels == clus]
    #         clus_idxs = clus_seqs.index.get_level_values(0).values
    #         rep = rep_method(sequences_idx=clus_idxs, **kwargs)
    #         n_seqs = clus_seqs.shape[0]
    #         # This is to sum over the index of sequences that have the sequence repetitions
    #         if self.unique:
    #             total_seqs = 0
    #             for seq in clus_seqs.index.values:
    #                 total_seqs += seq[1]
    #         else:
    #             total_seqs = n_seqs
    #
    #         cluster_percentage = total_seqs / self.n_sequences
    #         cluster_inf[clus] = (cluster_percentage, colors[clus], rep)
    #
    #     return cluster_inf


# data = np.array([[11, 12, 13, 14, 15], [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], [6, 7, 8, 12, 11],
#                  [11, 12, 13, 14, 15], [1, 2, 3, 4, 5]])
# data_df = pd.DataFrame(data=data)
# a = Sequences(data_df)
# a.dissimilarity_matrix()
# labels = np.array([0, 1, 2, 3, 0, 1])
# a.seq_representativeness(clus_labels=labels)
#
