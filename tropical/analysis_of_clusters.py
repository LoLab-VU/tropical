from __future__ import division
import numpy as np
import csv
import matplotlib.pyplot as plt
import colorsys
from scipy.optimize import curve_fit
from mpl_toolkits.axes_grid1 import make_axes_locatable
import os
import helper_functions as hf
from matplotlib.offsetbox import AnchoredText
import seaborn as sns
import collections
import numbers
from pysb.bng import generate_equations
# from scipy.stats import lognorm

plt.ioff()


def all_equal(iterator):
  try:
     iterator = iter(iterator)
     first = next(iterator)
     return all(np.array_equal(first, rest) for rest in iterator)
  except StopIteration:
     return True


class AnalysisCluster(object):

    """
    Class to generate the dynamics and visualize distributions of the species concentrations in the different clusters

    Parameters
    ----------
    model: pysb.Model
        Model passed to the constructor
    clusters: list-like
        Parameters in the clusters generated by TroPy
    sim_results: SimulationResult class
        SimulationResult object with the dynamic solutions of the model for all the parameter sets
    """
    def __init__(self, model, sim_results, clusters):

        self.model = model
        generate_equations(model)
        if all_equal(sim_results.tout):
            self.tspan = sim_results.tout[0]
        else:
            raise Exception('Analysis is not supported for simulations with different time spans')

        # get parameters
        self.all_parameters = sim_results.param_values

        # check clusters
        if isinstance(clusters, collections.Iterable):
            # check if clusters is a list of files containing the indices or idx of the IC that belong to that cluster
            if all(os.path.isfile(str(item)) for item in clusters):
                clus_values = {}
                number_pars = 0
                for i, clus in enumerate(clusters):
                    f = open(clus)
                    data = csv.reader(f)
                    pars_idx = [int(d[0]) for d in data]
                    clus_values[i] = pars_idx
                    number_pars += len(pars_idx)
                # self.clusters is a dictionary that contains the index of the parameter values that belong to different
                # clusters
                self.clusters = clus_values
                self.number_pars = number_pars
            elif all(isinstance(item, numbers.Number) for item in clusters):
                pars_clusters = clusters
                num_of_clusters = set(pars_clusters)
                clus_values = {}
                for j in num_of_clusters:
                    item_index = np.where(pars_clusters == j)
                    clus_values[j] = item_index[0]
                self.clusters = clus_values
                self.number_pars = len(pars_clusters)
            else:
                raise ValueError('Mixed formats is not supported')
        # check is clusters is a file that contains the indices of the clusters for each parameter set
        elif isinstance(clusters, str):
            if os.path.isfile(clusters):
                f = open(clusters)
                data = csv.reader(f)
                pars_clusters = np.array([int(d[0]) for d in data])
                num_of_clusters = set(pars_clusters)
                clus_values = {}
                for j in num_of_clusters:
                    item_index = np.where(pars_clusters == j)
                    clus_values[j] = item_index[0]
                self.clusters = clus_values
                self.number_pars = len(pars_clusters)

        elif clusters is None:
            no_clusters = {0: range(sim_results.nsims)}
            self.clusters = no_clusters
            self.number_pars = sim_results.nsims
        else:
            raise TypeError('wrong data structure')

        self.all_simulations = sim_results.all

    @staticmethod
    def curve_fit_ftn(function, species, xdata, ydata, **kwargs):
        """
        Fit simulation data to specific function

        Parameters
        ----------
        functions: callable
            functions that would be used for fitting the data
        species: int
            species whose trajectories will be fitted to a function
        xdata: list-like,
            x-axis data points (usually time span of the simulation)
        ydata: list-like,
            y-axis data points (usually concentration of species in time)
        kwargs: dict,
            Key arguments to use in curve-fit

        Returns
        -------
        Parameter values of the functions used to fit the data

        """
        if not callable(function):
            raise Exception('a function must be provided')
        results = curve_fit(function, xdata, ydata['__s{0}'.format(species)], p0=kwargs['p0'])[0]
        # FIXME this returns the fitting of only the first species
        return results

    def plot_dynamics_cluster_types(self, species, save_path='', species_to_fit=None, fit_ftn=None, norm=False, **kwargs):
        """
        Plots the dynamics of the species for each cluster

        Parameters
        ----------
        species: list-like
            Indices of PySB species that will be plotted
        save_path: str
            Path to file to save figures
        species_to_fit: int
            Index of species whose trajectory would be fitted to a function (fit_ftn)
        fit_ftn: list-like
            list of functions that will be used to fit the simulation results
        norm: boolean, optional
            Normalizes species by max value in simulation
        kwargs

        Returns
        -------

        """

        # creates a dictionary to store the different figures by cluster
        plots_dict = {}
        for sp in species:
            for clus in self.clusters:
                plots_dict['plot_sp{0}_cluster{1}'.format(sp, clus)] = plt.subplots()

        if norm:
            if species_to_fit:
                # checking if species_to_fit are present in the species that are going to be plotted
                sp_overlap = [ii for ii in species_to_fit if ii in species]
                if not sp_overlap:
                    raise ValueError('species_to_fit must be in model.species')

                for idx, clus in self.clusters.items():
                    ftn_result = []
                    for i, par_idx in enumerate(clus):
                        y = self.all_simulations[par_idx]
                        try:
                            ydata = y['__s{0}'.format(species_to_fit)]
                            result = curve_fit(f=fit_ftn, xdata=self.tspan, ydata=ydata, **kwargs)
                        except:
                            print ("Trajectory {0} can't be fitted".format(par_idx))
                        ftn_result.append(result)
                        for i_sp, sp in enumerate(species):
                            sp_max = y['__s{0}'.format(sp)].max()
                            plots_dict['plot_sp{0}_cluster{1}'.format(sp, idx)][1].plot(self.tspan,
                                                                                        y['__s{0}'.format(sp)] / sp_max,
                                                                                        color='blue', alpha=0.2)
                    for ind, sp_dist in enumerate(species_to_fit):
                        ax = plots_dict['plot_sp{0}_cluster{1}'.format(sp_dist, idx)][1]
                        divider = make_axes_locatable(ax)
                        axHistx = divider.append_axes("top", 1.2, pad=0.3, sharex=ax)
                        # axHisty = divider.append_axes("right", 1.2, pad=0.3, sharey=ax)
                        plt.setp(axHistx.get_xticklabels(),
                                 visible=False)  # + axHisty.get_yticklabels(), visible=False)

                        # This is specific for the time of death fitting in apoptosis
                        hist_data = hf.column(ftn_result, 1)
                        hist_data_filt = hist_data[(hist_data > 0) & (hist_data < self.tspan[-1])]

                        # shape, loc, scale = lognorm.fit(hist_data_filt, floc=0)
                        # pdf = lognorm.pdf(np.sort(hist_data_filt), shape, loc, scale)
                        shape = np.std(hist_data_filt)
                        scale = np.average(hist_data_filt)

                        pdf_pars = r'$\sigma$ ='+str(round(shape, 2))+'\n' r'$\mu$ ='+str(round(scale, 2))
                        anchored_text = AnchoredText(pdf_pars, loc=1, prop=dict(size=12))
                        axHistx.add_artist(anchored_text)
                        axHistx.hist(hist_data_filt, normed=True, bins=20)
                        axHistx.vlines(10230.96, -0.05, 1.05, color='r', linestyle=':', linewidth=2) # MOMP data
                        # axHistx.plot(np.sort(hist_data_filt), pdf) # log fitting to histogram data
                        for tl in axHistx.get_xticklabels():
                            tl.set_visible(False)
                        # yticks = [v for v in np.linspace(0, pdf.max(), 3)]
                        axHistx.set_ylim(0, 1.5e-3)
                        axHistx.ticklabel_format(axis='y', style='sci', scilimits=(-2, 2))
            else:
                for idx, clus in self.clusters.items():
                    y = self.all_simulations[clus]
                    for i_sp, sp in enumerate(species):
                        norm_trajectories = np.divide(y['__s{0}'.format(sp)].T, np.amax(y['__s{0}'.format(sp)], axis=1))
                        plots_dict['plot_sp{0}_cluster{1}'.format(sp, idx)][1].plot(self.tspan,
                                                                                    norm_trajectories,
                                                                                    color='blue',
                                                                                    alpha=0.2)
        else:
            for idx, clus in self.clusters.items():
                y = self.all_simulations[clus].T
                for i_sp, sp in enumerate(species):
                    plots_dict['plot_sp{0}_cluster{1}'.format(sp, idx)][1].plot(self.tspan, y['__s{0}'.format(sp)],
                                                                                color='blue',
                                                                                alpha=0.2)
        for ii, sp in enumerate(species):
            for clus in self.clusters:
                plots_dict['plot_sp{0}_cluster{1}'.format(sp, clus)][1].set_xlabel('Time')
                plots_dict['plot_sp{0}_cluster{1}'.format(sp, clus)][1].set_ylabel('Concentration')
                # plots_dict['plot_sp{0}_cluster{1}'.format(sp, clus)][1].set_xlim([0, 8])
                # plots_dict['plot_sp{0}_cluster{1}'.format(sp, clus)][1].set_ylim([0, 1])
                plots_dict['plot_sp{0}_cluster{1}'.format(sp, clus)][0].suptitle('{0}'.
                                                                                 format(self.model.species[sp]))
                final_save_path = os.path.join(save_path, 'plot_sp{0}_cluster{1}'.format(sp, clus))
                plots_dict['plot_sp{0}_cluster{1}'.format(sp, clus)][0].savefig(final_save_path+'.png',
                                                                                format='png', dpi=1000)
        return

    def hist_plot_clusters(self, ic_par_idxs, save_path=''):
        """
        Creates a plot for each cluster, and it has histograms of the species provided

        Parameters
        ----------
        ic_par_idxs: list-like
            Indices of the initial conditions that would be visualized
        save_path: str
            Path to where the file is going to be saved

        Returns
        -------

        """

        colors = self._get_colors(len(ic_par_idxs))
        plt.figure(1)
        for c_idx, clus in self.clusters.items():
            cluster_pars = self.all_parameters[clus]
            sp_ic_all = [0]*len(ic_par_idxs)
            sp_weights_all = [0]*len(ic_par_idxs)
            labels = [0]*len(ic_par_idxs)
            for idx, sp_ic in enumerate(ic_par_idxs):
                sp_ic_values = cluster_pars[:, sp_ic]
                sp_ic_weights = np.ones_like(sp_ic_values) / len(sp_ic_values)
                sp_ic_all[idx] = sp_ic_values
                sp_weights_all[idx] = sp_ic_weights
                labels[idx] = self.model.parameters[sp_ic].name
            plt.hist(sp_ic_all, weights=sp_weights_all, alpha=0.4, color=colors, label=labels)
            plt.xlabel('Concentration')
            plt.ylabel('Percentage')
            plt.legend(loc=0)
            final_save_path = os.path.join(save_path, 'plot_ic_type{0}'.format(c_idx))
            plt.savefig(final_save_path+'.png', format='png')
            plt.clf()
        return

    def violin_plot_sps(self, par_idxs, save_path=''):
        """
        Creates a plot for each paramater passed, and then creates violin plots for each cluster

        Parameters
        ----------
        par_idxs: list-like
            Indices of the parameters that would be visualized
        save_path: str
            Path to where the file is going to be saved

        Returns
        -------

        """

        for idx, sp_ic in enumerate(par_idxs):
            plt.figure(1)
            data_violin = [0]*len(self.clusters)
            d_count = 0
            for c_idx, clus in self.clusters.items():
                cluster_pars = self.all_parameters[clus]
                sp_ic_values = cluster_pars[:, sp_ic]
                data_violin[d_count] = sp_ic_values
                d_count += 1

            g = sns.violinplot(data=data_violin, orient='h', bw='silverman', cut=0, scale='area', inner='box')
            g.set_yticklabels(self.clusters.keys())
            plt.xlabel('Parameter Units')
            final_save_path = os.path.join(save_path, 'plot_sp_{0}'.format(self.model.parameters[sp_ic].name))
            plt.savefig(final_save_path+'.png', format='png')
            plt.clf()
        return

    def plot_sp_ic_overlap(self, ic_par_idxs, save_path=''):
        """
        Creates a stacked histogram with the distributions of each of the clusters for each initial condition provided

        Parameters
        ----------
        ic_par_idxs: list
            Indices of the initial conditions in model.parameter to plot
        save_path: str
            Path to save the file

        Returns
        -------

        """

        if type(ic_par_idxs) == int:
            ic_par_idxs = [ic_par_idxs]

        for ic in ic_par_idxs:
            plt.figure()
            sp_ic_values_all = self.all_parameters[:, ic]
            sp_ic_weights_all = np.ones_like(sp_ic_values_all) / len(sp_ic_values_all)
            n, bins, patches = plt.hist(sp_ic_values_all, weights=sp_ic_weights_all, bins=30, fill=False)

            cluster_ic_values = []
            cluster_ic_weights = []
            for c_idx, clus in self.clusters.items():
                cluster_pars = self.all_parameters[clus]
                sp_ic_values = cluster_pars[:, ic]
                sp_ic_weights = np.ones_like(sp_ic_values) / len(sp_ic_values_all)
                cluster_ic_values.append(sp_ic_values)
                cluster_ic_weights.append(sp_ic_weights)

            label = ['cluster_{0}, {1}%'.format(cl, (len(self.clusters[cl])/self.number_pars)*100)
                     for cl in self.clusters.keys()]
            plt.hist(cluster_ic_values, bins=bins, weights=cluster_ic_weights, stacked=True, label=label,
                     histtype='bar', ec='black')
            plt.xlabel('Concentration')
            plt.ylabel('Percentage')
            plt.title(self.model.parameters[ic].name)
            plt.legend(loc=0)

            final_save_path = os.path.join(save_path, 'plot_ic_overlap_{0}'.format(ic))
            plt.savefig(final_save_path+'.png', format='png')
        return

    def scatter_plot_pars(self, ic_par_idxs, cluster,  save_path=''):
        """

        Parameters
        ----------
        ic_par_idxs: list
            Indices of the parameters to visualized
        cluster: list-like
        save_path

        Returns
        -------

        """
        if isinstance(cluster, int):
            cluster_idxs = self.clusters[cluster]
        elif isinstance(cluster, collections.Iterable):
            cluster_idxs = cluster
        else:
            raise TypeError('format not supported')

        sp_ic_values1 = self.all_parameters[cluster_idxs, ic_par_idxs[0]]
        sp_ic_values2 = self.all_parameters[cluster_idxs, ic_par_idxs[1]]
        plt.figure()
        plt.scatter(sp_ic_values1, sp_ic_values2)
        ic_name0 = self.model.parameters[ic_par_idxs[0]].name
        ic_name1 = self.model.parameters[ic_par_idxs[1]].name
        plt.xlabel(ic_name0)
        plt.ylabel(ic_name1)
        final_save_path = os.path.join(save_path, 'scatter plot {0} and {1}, cluster {2}'.format(ic_name0, ic_name1,
                                                                                                 cluster))
        plt.savefig(final_save_path+'.png', format='png')

    @staticmethod
    def _get_colors(num_colors):
        colors = []
        for i in np.arange(0., 360., 360. / num_colors):
            hue = i / 360.
            lightness = (50 + np.random.rand() * 10) / 100.
            saturation = (90 + np.random.rand() * 10) / 100.
            colors.append(colorsys.hls_to_rgb(hue, lightness, saturation))
        return colors
