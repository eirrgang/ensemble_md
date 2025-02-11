####################################################################
#                                                                  #
#    ensemble_md,                                                  #
#    a python package for running GROMACS simulation ensembles     #
#                                                                  #
#    Written by Wei-Tse Hsu <wehs7661@colorado.edu>                #
#    Copyright (c) 2022 University of Colorado Boulder             #
#                                                                  #
####################################################################
import os
import sys
import time
import glob
import pyemma
import pymbar
import pickle
import natsort
import argparse
import warnings
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import rc
from datetime import datetime
from deeptime.markov.tools.analysis import is_transition_matrix
warnings.simplefilter(action='ignore', category=UserWarning)

from ensemble_md.utils import utils  # noqa: E402
from ensemble_md.analysis import analyze_traj  # noqa: E402
from ensemble_md.analysis import analyze_matrix  # noqa: E402
from ensemble_md.analysis import msm_analysis  # noqa: E402
from ensemble_md.analysis import analyze_free_energy  # noqa: E402
from ensemble_md.ensemble_EXE import EnsembleEXE  # noqa: E402
from ensemble_md.utils.exceptions import ParameterError  # noqa: E402


def initialize(args):
    parser = argparse.ArgumentParser(
        description='This code analyzes an ensemble of expanded ensemble. Note that the template MDP\
                file specified in the YAML file needs to be available in the working directory.')
    parser.add_argument('-y',
                        '--yaml',
                        type=str,
                        default='params.yaml',
                        help='The input YAML file used to run the EEXE simulation. (Default: params.yaml)')
    parser.add_argument('-o',
                        '--output',
                        type=str,
                        default='analyze_EEXE_log.txt',
                        help='The output log file that contains the analysis results of EEXE. \
                            (Default: analyze_EEXE_log.txt)')
    parser.add_argument('-rt',
                        '--rep_trajs',
                        type=str,
                        default='rep_trajs.npy',
                        help='The NPY file containing the replica-space trajectory. (Default: rep_trajs.npy)')
    parser.add_argument('-st',
                        '--state_trajs',
                        type=str,
                        default='state_trajs.npy',
                        help='The NPY file containing the stitched state-space trajectory. \
                            If the specified file is not found, the code will try to find all the trajectories and \
                            stitch them. (Default: state_trajs.npy)')
    parser.add_argument('-d',
                        '--dir',
                        default='analysis',
                        help='The name of the folder for storing the analysis results.')
    parser.add_argument('-m',
                        '--maxwarn',
                        type=int,
                        default=0,
                        help='The maximum number of warnings in parameter specification to be ignored.')
    args_parse = parser.parse_args(args)

    return args_parse


def main():
    t0 = time.time()
    args = initialize(sys.argv[1:])
    warnings.simplefilter(action='ignore', category=RuntimeWarning)
    sys.stdout = utils.Logger(logfile=args.output)
    sys.stderr = utils.Logger(logfile=args.output)
    section_idx = 0

    rc('font', **{
        'family': 'sans-serif',
        'sans-serif': ['DejaVu Sans'],
    })
    # Set the font used for MathJax - more on this later
    rc('mathtext', **{'default': 'regular'})
    plt.rc('font', family='serif')

    print(f'Current time: {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}')
    print(f'Command line: {" ".join(sys.argv)}')

    EEXE = EnsembleEXE(args.yaml)
    EEXE.print_params(params_analysis=True)

    for i in EEXE.warnings:
        print()
        print(f'{i}')
        print()

    if len(EEXE.warnings) > args.maxwarn:
        raise ParameterError(
            f"The execution failed due to warning(s) about parameter spcificaiton. Consider setting maxwarn in the input YAML file if you want to ignore them.")  # noqa: E501, F541

    # Check if the folder for saving the outputs exist. If not, create the folder.
    if os.path.exists(args.dir) is False:
        os.mkdir(args.dir)

    print('\nData analysis of the simulation ensemble')
    print('========================================')

    # Section 1. Analysis based on transitions between replicas
    print('[ Section 1. Analysis based on transitions between replicas ]')
    section_idx += 1

    # 1-0. Read in replica-space trajectories
    print('1-0. Reading in the replica-space trajectory ...')
    rep_trajs = np.load(args.rep_trajs)  # Shape: (n_sim, n_iter)

    # 1-1. Plot the replica-sapce trajectory
    print('1-1. Plotting transitions between replicas ...')
    dt_swap = EEXE.nst_sim * EEXE.dt    # dt for swapping replicas
    analyze_traj.plot_rep_trajs(rep_trajs, f'{args.dir}/rep_trajs.png', dt_swap)

    # 1-2. Plot the replica transition matrix
    print('1-2. Plotting the replica transition matrix (considering all configurations) ...')
    counts = [analyze_traj.traj2transmtx(rep_trajs[i], EEXE.n_sim, normalize=False) for i in range(len(rep_trajs))]
    reps_mtx = np.sum(counts, axis=0)  # First sum up the counts. This should be symmetric if n_ex=1. Otherwise it might not be. # noqa: E501
    reps_mtx /= np.sum(reps_mtx, axis=1)[:, None]   # and then normalize each row
    analyze_matrix.plot_matrix(reps_mtx, f'{args.dir}/rep_transmtx_allconfigs.png')

    # 1-3. Calculate the spectral gap for the replica transition amtrix
    spectral_gap, eig_vals = analyze_matrix.calc_spectral_gap(reps_mtx)
    print(f'1-3. The spectral gap of the replica transition matrix: {spectral_gap:.3f}')

    # Section 2. Analysis based on transitions between states
    print('\n[ Section 2. Analysis based on transitions between states ]')
    section_idx += 1

    # 2-0. Stitch the trajectories for each configuration
    if os.path.isfile(args.state_trajs) is True:
        print('2-0. Reading in the stitched state-space trajectory ...')
        state_trajs = np.load(args.state_trajs)
    else:
        # This may take a while.
        print('2-0. Stitching trajectories for each configuration from dhdl files ...')
        dhdl_files = [natsort.natsorted(glob.glob(f'sim_{i}/iteration_*/*dhdl*xvg')) for i in range(EEXE.n_sim)]
        shifts = np.arange(EEXE.n_sim) * EEXE.s
        state_trajs = analyze_traj.stitch_trajs(dhdl_files, rep_trajs, shifts=shifts)  # length: the number of replicas
        print('     Saving state_trajs.npy ...')
        np.save('state_trajs.npy', state_trajs)   # save the stithced trajectories

    # 2-1. Plot the state-space trajectory
    print('\n2-1. Plotting transitions between different alchemical states ...')
    dt_traj = EEXE.dt * EEXE.template['nstdhdl']  # in ps
    analyze_traj.plot_state_trajs(state_trajs, EEXE.state_ranges, f'{args.dir}/state_trajs.png', dt_traj)

    # 2-2. Plot the histograms for the states
    print('\n2-2. Plotting the histograms of the state index ...')
    analyze_traj.plot_state_hist(state_trajs, EEXE.state_ranges, f'{args.dir}/state_hist.png')

    # 2-3. Plot the overall state transition matrices calculated from the state-space trajectories
    print('\n2-3. Plotting the overall state transition matrices ...')
    mtx_list = []
    for i in range(EEXE.n_sim):
        mtx = analyze_traj.traj2transmtx(state_trajs[i], EEXE.n_tot)
        mtx_list.append(mtx)
        analyze_matrix.plot_matrix(mtx, f'{args.dir}/config_{i}_state_transmtx.png')

    # 2-4. For each configurration, calculate the spectral gap of the overall transition matrix obtained in step 2-2.
    print('\n2-4. Calculating the spectral gap of the state transition matrices ...')
    spectral_gaps, eig_vals = [analyze_matrix.calc_spectral_gap(mtx) for mtx in mtx_list]
    if None not in spectral_gaps:
        for i in range(EEXE.n_sim):
            print(f'   - Configuration {i}: {spectral_gaps[i]:.3f} (λ_1: {eig_vals[0]:.5f}, λ_2: {eig_vals[1]:.5f})')
        print(f'   - Average of the above: {np.mean(spectral_gaps):.3f} (std: {np.std(spectral_gaps, ddof=1):.3f})')

    # 2-5. For each configuration, calculate the stationary distribution from the overall transition matrix obtained in step 2-2.  # noqa: E501
    print('\n2-5. Calculating the stationary distributions ...')
    pi_list = [analyze_matrix.calc_equil_prob(mtx) for mtx in mtx_list]
    if any([x is None for x in pi_list]):
        pass  # None is in the list
    else:
        for i in range(EEXE.n_sim):
            print(f'   - Configuration {i}: {", ".join([f"{j:.3f}" for j in pi_list[i].reshape(-1)])}')
        if len({len(i) for i in pi_list}) == 1:  # all lists in pi_list have the same length
            print(f'   - Average of the above: {", ".join([f"{i:.3f}" for i in np.mean(pi_list, axis=0).reshape(-1)])}')  # noqa: E501

    # 2-6. Calculate the state index correlation time for each configuration (this step is more time-consuming one)
    print('\n2-6. Calculating the state index correlation time ...')
    tau_list = [(pymbar.timeseries.statistical_inefficiency(state_trajs[i], fast=True) - 1) / 2 * dt_traj for i in range(EEXE.n_sim)]  # noqa: E501
    for i in range(EEXE.n_sim):
        print(f'   - Configuration {i}: {tau_list[i]:.1f} ps')
    print(f'   - Average of the above: {np.mean(tau_list):.1f} ps (std: {np.std(tau_list, ddof=1):.1f} ps)')

    # 2-7. Calculate transit times for each configuration
    print('\n2-7. Plotting the average transit times ...')
    t_0k_list, t_k0_list, t_roundtrip_list, units = analyze_traj.plot_transit_time(state_trajs, EEXE.n_tot, dt=dt_traj, folder=args.dir)  # noqa: E501
    meta_list = [t_0k_list, t_k0_list, t_roundtrip_list]
    t_names = [
        '\n   - Average transit time from states 0 to k',
        '\n   - Average transit time from states k to 0',
        '\n   - Average round-trip time',
    ]
    for i in range(len(meta_list)):
        t_list = meta_list[i]
        print(t_names[i])
        for j in range(len(t_list)):
            if t_list[j] is None:
                print(f'     - Configuration {j}: Skipped')
            else:
                print(f'     - Configuration {j} ({len(t_list[j])} events): {np.mean(t_list[j]):.2f} {units}')
        print(f'     - Average of the above: {np.mean([np.mean(i) for i in t_list]):.2f} {units} (std: {np.std([np.mean(i) for i in t_list], ddof=1):.2f} {units})')  # noqa: E501

    if EEXE.msm is True:
        section_idx += 1

        # Section 3. Analysis based on Markov state models
        print('\n[ Section 3. Analysis based on Markov state models ]')

        # 3-1. Plot the implied timescale as a function of lag time
        print('\n3-1. Plotting the implied timescale as a function of lag time for all configurations ...')
        lags = np.arange(EEXE.lag_spacing, EEXE.lag_max + EEXE.lag_spacing, EEXE.lag_spacing)
        # lags could also be None and decided automatically. Could consider using that.
        ts_list = msm_analysis.plot_its(state_trajs, lags, fig_name=f'{args.dir}/implied_timescales.png', dt=dt_traj, units='ps')  # noqa: E501

        # 3-2. Decide a lag time for building MSM
        print('\n3-2. Calculating recommended lag times for building MSMs ...')
        chosen_lags = msm_analysis.decide_lagtimes(ts_list)
        print('     \nSuggested lag times for each configuration:')
        for i in range(len(chosen_lags)):
            print(f'       - Configuration {i}: {chosen_lags[i] * dt_traj:.1f} ps')

        # 3-3. Build a Bayesian MSM and perform a CK test for each configuration to validate the models
        print('\n3-3. Building Bayesian MSMs for the state-space trajectory for each configuration ...')
        print('     Performing a Chapman-Kolmogorov test on each trajectory ...')
        models = [pyemma.msm.bayesian_markov_model(state_trajs[i], chosen_lags[i], dt_traj=f'{dt_traj} ps', show_progress=False) for i in range(EEXE.n_sim)]  # noqa: E501

        for i in range(len(models)):
            print(f'     Plotting the CK-test results for configuration {i} ...')
            mlags = 5  # this maps to 5 (mlags: multiples of lag times for testing the model)
            nsets = models[i].nstates  # number of metastable states.
            # Note that nstates is the number of unique states in the input trajectores counted with the effective mode
            # (see the documentation) Therefore, if a system barely sampled some of the states, those states will
            # not be counted as involved in the transition matrix (i.e. not in the active set). To check the
            # active states, use models[i].active_set. If the system sampled all states frequently,
            # models[i].active_set should be equal to np.unique(state_trajs[i]) and both lengths should be
            # EEXE.n_tot. I'm not sure why the attribute nstates_full is not always EEXE.n_tot but is less
            # relevant here.
            cktest = models[i].cktest(nsets=nsets, mlags=mlags, show_progress=False)
            pyemma.plots.plot_cktest(cktest, dt=dt_traj, units='ps')
            plt.tight_layout(rect=[0, 0.03, 1, 0.98])
            plt.savefig(f'{args.dir}/config_{i}_CKtest.png', dpi=600)

        # Additionally, check if the sampling is poor for each configuration
        for i in range(len(models)):
            if models[i].nstates != EEXE.n_tot:
                print(f'     Note: The sampling of configuration {i} was poor.')

        # 3-4. Plot the state transition matrices estimated with the specified lag times in MSMs
        print('\n3-4. Plotting the overall transition matrices for all configurations and their average ...')
        mtx_list = [m.transition_matrix for m in models]
        mtx_list_modified = []  # just for plotting (if all trajs sampled the fulle range frequently, this will be the same as mtx_list)  # noqa: E501
        for i in range(len(mtx_list)):
            # check if each mtx in mtx_list spans the full alchemical range. (If the system did not visit
            # certain states, the dimension will be less than EEXE.n_tot * EEXE.n_tot. In this case, we
            # add rows and columns of 0. Note that the modified matrix will not be a transition matrix,
            # so this is only for plotting. For later analysis such as spectral gap calculation, we
            # will just use the unmodified matrices.
            if mtx_list[i].shape != (EEXE.n_tot, EEXE.n_tot):  # add rows and columns of 0
                sampled = models[i].active_set
                missing = list(set(range(EEXE.n_tot)) - set(sampled))  # states not visited

                # figure out which end we should stack rows/columns to
                n_1 = sum(missing > max(sampled))   # add rows/columns to the end of large state indices
                n_2 = sum(missing < min(sampled))   # add rows/columns to the end of small state indices

                # Below we first add a column and a row and so on
                mtx = mtx_list[i]
                for j in range(n_1):
                    d = mtx.shape[0]  # size of the square matrix
                    mtx = np.hstack((mtx, np.zeros([d, 1])))
                    mtx = np.vstack((mtx, np.zeros([1, d + 1])))
                for j in range(n_2):
                    d = mtx.shape[0]  # size of the square matrix
                    mtx = np.hstack((np.zeros([d, 1]), mtx))
                    mtx = np.vstack((np.zeros([1, d + 1]), mtx))
                mtx_list_modified.append(mtx)
            else:
                mtx_list_modified.append(mtx_list[i])

        avg_mtx = np.mean(mtx_list_modified, axis=0)
        print('     Saving transmtx.npy (plotted transition matrices)...')
        np.save(f'{args.dir}/transmtx.npy', mtx_list_modified)

        for i in range(EEXE.n_sim):
            analyze_matrix.plot_matrix(mtx_list[i], f'{args.dir}/config_{i}_state_transmtx_msm.png')
        analyze_matrix.plot_matrix(avg_mtx, f'{args.dir}/state_transmtx_avg_msm.png')

        # Note that if a configuration never visited a certain replica, the rows in the alchemical range of that
        # replica will only have 0 entries. Below we check if this is the case.
        print('     Checking the sum of each row of each transition matrix is 1 ...')
        is_transmtx = [is_transition_matrix(i) for i in mtx_list]
        for i in range(len(is_transmtx)):
            if is_transmtx[i] is False:
                print(f'     The sums of the rows are not 1 (but {np.sum(mtx_list[i], axis=1)}) for the transition matrix of configuration {i}.')  # noqa: E501

        # 3-5. Calculate the spectral gap from the transition matrix of each configuration
        print('\n3-5. Calculating the spectral gap of the state transition matrices obtained from MSMs ...')
        spectral_gaps, eig_vals = [analyze_matrix.calc_spectral_gap(mtx) for mtx in mtx_list]
        for i in range(EEXE.n_sim):
            print(f'       - Configuration {i}: {spectral_gaps[i]:.3f}')

        # 3-6. Calculate the stationary distribution for each configuration
        print('\n3-6. Calculating the stationary distributions from the transition matrices obtained from MSMs ...')
        pi_list = [m.pi for m in models]
        for i in range(EEXE.n_sim):
            print(f'       - Configuration {i}: {", ".join([f"{j:.3f}" for j in pi_list[i]])}')
        if len({len(i) for i in pi_list}) == 1:  # all lists in pi_list have the same length
            print(f'       - Average of the above: {", ".join([f"{i:.3f}" for i in np.mean(pi_list, axis=0)])}')

        # 3-7. Calculate the mean first-passage time (MFPT) from each MSM
        print('\n3-7. Calculating the mean first-passage times (MFPTs) ...')
        # note that it's not m.mfpt(min(m.active_set), max(m.active_set)) as the input to mfpt should be indices
        # though sometimes these two could be same.
        mfpt_list = [m.mfpt(0, m.nstates - 1) for m in models]
        for i in range(EEXE.n_sim):
            print(f'       - Configuration {i}: {mfpt_list[i]:.1f} ps')
        print(f'       - Average of the above: {np.mean(mfpt_list):.1f} ps (std: {np.std(mfpt_list, ddof=1):.1f} ps)')

        # 3-8. Calculate the state index correlation time for each configuration
        print('\n3-8. Plotting the state index correlation times for all configurations ...')
        msm_analysis.plot_acf(models, EEXE.n_tot, f'{args.dir}/state_ACF.png')

    # Section 4 (or Section 3). Free energy calculations
    if EEXE.free_energy is True:
        section_idx += 1
        print(f'\n[ Section {section_idx}. Free energy calculations ]')

        u_nk_list, dHdl_list = [], []

        if os.path.isfile(f'{args.dir}/u_nk_data.pickle') is True:
            print('Loading the preprocessed data u_nk ...')
            with open(f'{args.dir}/u_nk_data.pickle', 'rb') as handle:
                u_nk_list = pickle.load(handle)

        if os.path.isfile(f'{args.dir}/dHdl_data.pickle') is True:
            print('Loading the preprocessed data dHdl ...')
            with open(f'{args.dir}/dHdl_data.pickle', 'rb') as handle:
                dHdl_list = pickle.load(handle)

        if u_nk_list == [] and dHdl_list == []:
            for i in range(EEXE.n_sim):
                print(f'Reading dhdl files of replica {i} ...')
                files = natsort.natsorted(glob.glob(f'sim_{i}/iteration_*/*dhdl*xvg'))
                u_nk, dHdl = analyze_free_energy.preprocess_data(files, EEXE.temp, EEXE.df_spacing, EEXE.get_u_nk, EEXE.get_dHdl)  # noqa: E501
                u_nk_list.append(u_nk)
                dHdl_list.append(dHdl)

            with open(f'{args.dir}/u_nk_data.pickle', 'wb') as handle:
                pickle.dump(u_nk_list, handle, protocol=pickle.HIGHEST_PROTOCOL)

            with open(f'{args.dir}/dHdl_data.pickle', 'wb') as handle:
                pickle.dump(dHdl_list, handle, protocol=pickle.HIGHEST_PROTOCOL)

        state_ranges = [list(i) for i in EEXE.state_ranges]
        if EEXE.get_u_nk is True:
            f, f_err = analyze_free_energy.calculate_free_energy(u_nk_list, state_ranges, EEXE.df_method, EEXE.err_method, EEXE.n_bootstrap, EEXE.seed)  # noqa: E501
        else:
            f, f_err = analyze_free_energy.calculate_free_energy(dHdl_list, state_ranges, EEXE.df_method, EEXE.err_method, EEXE.n_bootstrap, EEXE.seed)  # noqa: E501

        print('Plotting the full-range free energy profile ...')
        analyze_free_energy.plot_free_energy(f, f_err, f'{args.dir}/free_energy_profile.png')

        print('The full-range free energy profile averaged over all replicas:')
        print(f"  {', '.join(f'{f[i]: .3f} +/- {f_err[i]: .3f} kT' for i in range(EEXE.n_tot))}")
        print(f'The free energy difference between the coupled and decoupled states: {f[-1]: .3f} +/- {f_err[-1]: .3f} kT')  # noqa: E501

    # Section 4. Calculate the time spent in GROMACS (This could take a while.)
    t_wall_tot, t_sync = utils.analyze_EEXE_time()
    print(f'\nTotal wall time GROMACS spent to finish all iterations: {utils.format_time(t_wall_tot)}')
    print(f'Total time spent in syncrhonizing all replicas: {utils.format_time(t_sync)}')

    print(f'\nTime elpased: {utils.format_time(time.time() - t0)}')
