import os
import sys
import time
from argparse import Namespace

import gym
from baselines.a2c.runner import Runner
from baselines.common import set_global_seeds
from baselines.common.cmd_util import make_env, make_vec_env
from baselines.common.policies import build_policy
from baselines.a2c.a2c import Model
from baselines.common import set_global_seeds, explained_variance
from baselines.ppo2.ppo2 import safemean
# sys.path.append('./baselines')
from baselines.common.vec_env import DummyVecEnv, VecEnv
from baselines.common.vec_env.test_vec_env import SimpleEnv
from baselines.run import build_env

from hx_controller.haxball_gym import Haxball
from hx_controller.haxball_vecenv import HaxballVecEnv, HaxballSubProcVecEnv
from hx_controller.openai_model_torneo import A2CModel
from simulator import create_start_conditions
import numpy as np
from collections import deque


if __name__ == '__main__':
    # def build_env():
    #     # gameplay = create_start_conditions()
    #     # env = Haxball(gameplay=gameplay)
    #     env = SimpleEnv(0, (5, ), np.float32)
    #     return env

    # gameplay = create_start_conditions()
    try:
        res = gym.spec('haxball-v0')
    except:
        gym.register(id='haxball-v0', entry_point='hx_controller.haxball_gym:Haxball', kwargs=dict(gameplay=None, max_ticks=2400))

    # args_namespace = Namespace(
    #     alg='a2c',
    #     # env='haxball-v0',
    #     env='PongNoFrameskip-v4',
    #     env_type=None, gamestate=None, network=None, num_env=None, num_timesteps=1000000.0, play=False, reward_scale=1.0, save_path=None, save_video_interval=0, save_video_length=200, seed=None)
    # env = build_env(args_namespace)

    args_namespace = Namespace(
        alg='a2c',
        env='haxball-v0',
        num_env=None,
        # env='PongNoFrameskip-v4',
        env_type=None, gamestate=None, network=None, num_timesteps=1000000.0, play=False,
        reward_scale=1.0, save_path=None, save_video_interval=0, save_video_length=200, seed=None)
    # env2 = build_env(args_namespace)

    try:
        from mpi4py import MPI
    except ImportError:
        MPI = None
    from baselines import logger
    def make_vec_env(env_id, env_type, num_env, seed,
                     wrapper_kwargs=None,
                     start_index=0,
                     reward_scale=1.0,
                     flatten_dict_observations=True,
                     gamestate=None):
        """
        Create a wrapped, monitored SubprocVecEnv for Atari and MuJoCo.
        """
        wrapper_kwargs = wrapper_kwargs or {}
        mpi_rank = MPI.COMM_WORLD.Get_rank() if MPI else 0
        seed = seed + 10000 * mpi_rank if seed is not None else None
        logger_dir = logger.get_dir()

        def make_thunk(rank):
            return lambda: make_env(
                env_id=env_id,
                env_type=env_type,
                mpi_rank=mpi_rank,
                subrank=rank,
                seed=seed,
                reward_scale=reward_scale,
                gamestate=gamestate,
                flatten_dict_observations=flatten_dict_observations,
                wrapper_kwargs=wrapper_kwargs,
                logger_dir=logger_dir
            )

        set_global_seeds(seed)
        return DummyVecEnv([make_thunk(i + start_index) for i in range(num_env)])

    nsteps = 1
    gamma = 0.99
    nenvs = 100
    total_timesteps = int(15e6)
    log_interval = 100
    load_path = None
    load_path = 'ciao.h5'
    play = bool(int(os.getenv('PLAY', False)))
    play = False
    if play:
        nenvs = 2

    # env = make_vec_env(env_id='haxball-v0', env_type=None, num_env=nenvs, seed=None)
    # env = HaxballVecEnv(num_fields=nenvs, max_ticks=2400*2)
    env = HaxballSubProcVecEnv(num_fields=nenvs, max_ticks=int(60*3*(1/0.1)))
    # env = make_vec_env(env_id='PongNoFrameskip-v4', env_type=None, num_env=nenvs, seed=0)
    # policy = build_policy(env=env, policy_network='lstm')#, num_layers=4, num_hidden=128)
    policy = build_policy(env=env, policy_network='mlp', num_layers=4, num_hidden=256)
    # policy2 = build_policy(env=env2, policy_network='mlp')

    model = A2CModel(policy, env=env, nsteps=nsteps, ent_coef=0.05, total_timesteps=total_timesteps)# 0.005) #, vf_coef=0.0)
    if load_path is not None and os.path.exists(load_path):
        model.load(load_path)

    if play:
        logger.log("Running trained model")
        obs = env.reset()

        state = model.initial_state if hasattr(model, 'initial_state') else None
        dones = np.zeros((1,))

        episode_rew = 0
        while True:
            if state is not None:
                actions, _, state, _ = model.step(obs, S=state, M=dones)
            else:
                actions, rew, _, _ = model.step(obs)

            obs, rew, done, _ = env.step(actions)
            episode_rew += rew[0] if isinstance(env, VecEnv) else rew
            env.render()
            done = done.any() if isinstance(done, np.ndarray) else done
            if done:
                print('episode_rew={}'.format(episode_rew))
                episode_rew = 0
                obs = env.reset()

    # Instantiate the runner object
    runner = Runner(env, model, nsteps=nsteps, gamma=gamma)
    epinfobuf = deque(maxlen=100)

    # Calculate the batch_size
    nbatch = nenvs * nsteps

    # Start total timer
    tstart = time.time()
    last_rewards = []

    for update in range(1, total_timesteps // nbatch + 1):
        # Get mini batch of experiences
        obs, states, rewards, masks, actions, values, epinfos = runner.run()

        # invert
        inv_obs = env.invert_states(obs)
        obs = np.vstack((obs, inv_obs))
        rewards = np.hstack((rewards, rewards))
        masks = np.hstack((masks, masks))
        inv_actions = env.invert_actions(actions)
        actions = np.hstack((actions, inv_actions))
        values = np.hstack((values, values))

        epinfobuf.extend(epinfos)

        # policy_loss, value_loss, policy_entropy = model.train(inv_obs, states, rewards, masks, inv_actions, values)
        policy_loss, value_loss, policy_entropy = model.train(obs, states, rewards, masks, actions, values)
        nseconds = time.time() - tstart

        # last_rewards += list(rewards)
        # last_rewards = last_rewards[-20000:]
        # Calculate the fps (frame per second)
        fps = int((update * nbatch) / nseconds)
        if update % log_interval == 0 or update == 1:
            # Calculates if value function is a good predicator of the returns (ev > 1)
            # or if it's just worse than predicting nothing (ev =< 0)
            ev = explained_variance(values, rewards)
            logger.record_tabular("nupdates", update)
            logger.record_tabular("total_timesteps", update * nbatch)
            logger.record_tabular('rewards', np.mean(rewards))
            logger.record_tabular('values', np.mean(values))
            logger.record_tabular("fps", fps)
            logger.record_tabular("policy_entropy", float(policy_entropy))
            logger.record_tabular("value_loss", float(value_loss))
            logger.record_tabular("explained_variance", float(ev))
            logger.record_tabular("eprewmean", safemean([epinfo['r'] for epinfo in epinfobuf]))
            logger.record_tabular("eplenmean", safemean([epinfo['l'] for epinfo in epinfobuf]))
            logger.dump_tabular()
        if update % 500 == 0:
            model.save(load_path)