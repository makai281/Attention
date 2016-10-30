from __future__ import division
from __future__ import print_function

from datetime import datetime
from data import data_iterator
from data import read_vocabulary

import tensorflow as tf
import numpy as np
import math
import os


class AttentionNN(object):
    def __init__(self, config, sess):
        self.sess          = sess
        self.hidden_size   = config.hidden_size
        self.num_layers    = config.num_layers
        self.batch_size    = config.batch_size
        self.max_size      = config.max_size
        self.epochs        = config.epochs
        self.s_nwords      = config.s_nwords
        self.t_nwords      = config.t_nwords
        self.show          = config.show
        self.minval        = config.minval
        self.maxval        = config.maxval
        self.lr_init       = config.lr_init
        self.max_grad_norm = config.max_grad_norm

        self.source_data_path  = config.source_data_path
        self.target_data_path  = config.target_data_path
        self.source_vocab_path = config.source_vocab_path
        self.target_vocab_path = config.target_vocab_path
        self.checkpoint_dir    = config.checkpoint_dir

        if not os.path.isdir(self.checkpoint_dir):
            raise Exception("[!] Directory {} not found".format(self.checkpoint_dir))

        self.source = tf.placeholder(tf.int32, [self.batch_size, self.max_size], name="source")
        self.target = tf.placeholder(tf.int32, [self.batch_size, self.max_size], name="target")

        self.build_model()

    def build_model(self):
        self.global_step = tf.Variable(0, trainable=False, name="global_step")
        self.lr = tf.Variable(self.lr_init, trainable=False, name="lr")

        with tf.variable_scope("encoder"):
            self.s_emb = tf.Variable(tf.random_uniform([self.s_nwords, self.hidden_size],
                                     minval=self.minval, maxval=self.maxval), name="embedding")
            cell = tf.nn.rnn_cell.BasicLSTMCell(self.hidden_size, state_is_tuple=True)
            self.encoder = tf.nn.rnn_cell.MultiRNNCell([cell]*self.num_layers, state_is_tuple=True)

        with tf.variable_scope("decoder"):
            self.t_emb = tf.Variable(tf.random_uniform([self.t_nwords, self.hidden_size],
                                     minval=self.minval, maxval=self.maxval), name="embedding")
            cell = tf.nn.rnn_cell.BasicLSTMCell(self.hidden_size, state_is_tuple=True)
            self.decoder = tf.nn.rnn_cell.MultiRNNCell([cell]*self.num_layers, state_is_tuple=True)

        with tf.variable_scope("proj"):
            self.proj_W = tf.Variable(tf.random_uniform([self.hidden_size, self.t_nwords],
                                      minval=self.minval, maxval=self.maxval), name="W")
            self.proj_b = tf.Variable(tf.random_uniform([self.t_nwords],
                                      minval=self.minval, maxval=self.maxval), name="b")

        source_idxs = tf.split(1, self.max_size, self.source)
        initial_state = self.encoder.zero_state(self.batch_size, tf.float32)
        s = initial_state
        with tf.variable_scope("encoder"):
            for t in xrange(self.max_size):
                x = tf.nn.embedding_lookup(self.s_emb, source_idxs[t])
                x = tf.squeeze(x)
                if t > 0: tf.get_variable_scope().reuse_variables()
                hs = self.encoder(x, s)
                s = hs[1]

        logits = []
        probs  = []
        target_idxs = tf.split(1, self.max_size, self.target)
        # s is now final encoding hidden state
        with tf.variable_scope("decoder"):
            for t in xrange(self.max_size):
                x = tf.nn.embedding_lookup(self.t_emb, target_idxs[t])
                x = tf.squeeze(x)
                if t > 0: tf.get_variable_scope().reuse_variables()
                hs = self.decoder(x, s)
                s = hs[1]

                logit = tf.batch_matmul(hs[0], self.proj_W) + self.proj_b
                probs.append(tf.nn.softmax(logit))
                logits.append(logit)

        logits     = logits[:-1]
        targets    = target_idxs[1:]
        weights    = [tf.ones([self.batch_size]) for _ in xrange(self.max_size - 1)]
        self.loss  = tf.nn.seq2seq.sequence_loss(logits, targets, weights)
        self.optim = tf.train.GradientDescentOptimizer(self.lr).minimize(self.loss)
        inc = self.global_step.assign_add(1)

        # TODO: renormalize gradients instead of clip
        opt = tf.train.GradientDescentOptimizer(self.lr)
        trainable_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)
        gvs = opt.compute_gradients(self.loss, [v for v in trainable_vars])
        clipped_gvs = [(tf.clip_by_norm(g, self.max_grad_norm), v) for g,v in gvs]
        with tf.control_dependencies([inc]):
            self.optim = opt.apply_gradients(clipped_gvs)

        self.sess.run(tf.initialize_all_variables())
        tf.scalar_summary("loss", self.loss)
        self.saver = tf.train.Saver()

    def get_model_name(self):
        date = datetime.now()
        return "attention-{}-{}-{}".format(date.month, date.day, date.hour)

    def train(self):
        data_size = len(open(self.source_data_path).readlines())
        N = int(math.ceil(data_size/self.batch_size))
        merged_sum = tf.merge_all_summaries()
        writer = tf.train.SummaryWriter("./logs/{}".format(self.get_model_name()),
                                        self.sess.graph)

        if self.show:
            from utils import ProgressBar
            bar = ProgressBar("Train", max=self.epochs*N)

        for epoch in xrange(self.epochs):
            iterator = data_iterator(self.source_data_path,
                                     self.target_data_path,
                                     read_vocabulary(self.source_vocab_path),
                                     read_vocabulary(self.target_vocab_path),
                                     self.max_size, self.batch_size)
            i = 0
            total_loss = 0.
            for dsource, dtarget in iterator:
                if self.show: bar.next()
                outputs = self.sess.run([self.loss, self.global_step, self.optim, merged_sum],
                                        feed_dict={self.source: dsource,
                                                   self.target: dtarget})
                loss = outputs[0]
                total_loss += loss
                if i % 2 == 0:
                    writer.add_summary(outputs[-1], N*epoch + i)
                i += 1

            step = outputs[1]
            self.saver.save(self.sess,
                            os.path.join(self.checkpoint_dir, self.get_model_name()),
                            global_step=step.astype(int))
            # without dropout after with, with dropout after 8
            if epoch > 5:
                self.lr_init = self.lr_init/2
                self.lr.assign(self.lr_init).eval()
        if self.show:
            bar.finish()
            print("")
        print("Loss: {}".format(total_loss/N))

    def test(self, source_data_path, target_data_path):
        data_size = len(open(source_data_path).readlines())
        N = int(math.ceil(data_size/self.batch_size))

        if self.show:
            from utils import ProgressBar
            bar = ProgressBar("Test", max=N)

        iterator = data_iterator(source_data_path,
                                 target_data_path,
                                 read_vocabulary(self.source_vocab_path),
                                 read_vocabulary(self.target_vocab_path),
                                 self.max_size, self.batch_size)

        total_loss = 0
        for dsource, dtarget in iterator:
            if self.show: bar.next()
            loss = self.sess.run([self.loss],
                                 feed_dict={self.source: dsource,
                                            self.target: dtarget})
            total_loss += loss[0]

        if self.show:
            bar.finish()
            print("")
        total_loss /= N
        perplexity = np.exp(total_loss)
        return perplexity

    def load(self):
        print("[*] Reading checkpoints...")
        ckpt = tf.train.get_checkpoint_state(self.checkpoint_dir)
        if ckpt and ckpt.model_checkpoint_path:
            self.saver.restore(self.sess, ckpt.model_checkpoint_path)
        else:
            raise Exception("[!] No checkpoint found")
