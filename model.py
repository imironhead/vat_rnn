"""
"""
import numpy as np
import tensorflow as tf


class VadModel(object):
    """
    """
    def __init__(self, params):
        """
        """
        self._session_name = params.get_session_name()

        self._checkpoint_source_path = tf.train.latest_checkpoint(
            params.get_checkpoint_source_path())
        self._checkpoint_target_path = \
            params.get_checkpoint_target_path()

        # number of samples of a sequence for training
        self._training_sequence_size = params.get_rnn_sequence_length()

        # wav feature dimension
        self._wav_cepstrum_size = params.get_wav_cepstrum_size()

        # how many samples to delay
        self._srt_delay_size = params.get_srt_delay_size()

        # batch sample weights for delay
        self._batch_sample_weights = tf.placeholder(
            tf.float32,
            [self._training_sequence_size])

        # source data, wav features batch
        self._source_data = tf.placeholder(
            tf.float32,
            [None, self._training_sequence_size, self._wav_cepstrum_size])

        # label data, srt features batch
        self._target_data = tf.placeholder(
            tf.int32, [None, self._training_sequence_size])

        #
        source = self.build_nn_before_rnn(params, self._source_data)

        # split source to feature list
        source = tf.reshape(
            source,
            [-1, self._training_sequence_size * self._wav_cepstrum_size])

        source = tf.split(1, self._training_sequence_size, source)

        # batch size
        batch_size = tf.shape(self._source_data)[0]

        # RNN cell factory
        rnn_cell = tf.nn.rnn_cell.BasicLSTMCell(
            params.get_rnn_unit_num(), state_is_tuple=True)

        self._state = rnn_cell.zero_state(batch_size, tf.float32)

        # build rnn
        outputs, self._last_state = tf.nn.seq2seq.rnn_decoder(
            source, self._state, rnn_cell)

        #
        logits = self.build_nn_after_rnn(params, outputs)

        # final
        probabilities = tf.nn.softmax(logits)

        # weights to filter delay
        wgts = tf.tile(self._batch_sample_weights, [batch_size])

        # rnn loss
        total_loss = tf.nn.seq2seq.sequence_loss_by_example(
            [logits], [tf.reshape(self._target_data, [-1])], [wgts])

        # regularization losses

        # cost
        total_size = tf.reduce_sum(wgts)

        self._loss = tf.reduce_sum(total_loss) / total_size

        # global step
        initializer_z = tf.constant_initializer(0.0)

        self._global_step = tf.get_variable(
            "gstep", [], trainable=False, initializer=initializer_z)

        # trainer
        self._trainer = self.build_optimizer(params)
        self._trainer = self._trainer.minimize(
            self._loss, global_step=self._global_step)

        # correctness
        correctness = tf.equal(tf.cast(
            tf.argmax(probabilities, 1), tf.int32),
            tf.reshape(self._target_data, [-1]))
        correctness = tf.cast(correctness, tf.float32)
        correctness = tf.mul(correctness, wgts)

        self._judge = tf.reduce_sum(correctness) / total_size

        #
        self._session = tf.Session()

        # restore check point
        if self._checkpoint_source_path is not None:
            tf.train.Saver().restore(
                self._session, self._checkpoint_source_path)
        else:
            self._session.run(tf.global_variables_initializer())

        tf.summary.scalar('training loss', self._loss)
        tf.summary.scalar('training accuracy', self._judge)

        self._summaries = tf.summary.merge_all()
        self._reporter = tf.summary.FileWriter(
            params.get_tensorboard_log_path(), self._session.graph)

    def build_nn_before_rnn(self, params, source):
        """
        """
        dims = params.get_hidden_layer_dim_before_rnn()
        size = self._wav_cepstrum_size

        if len(dims) > 0:
            source = tf.reshape(source, [-1, self._wav_cepstrum_size])

        for idx, dim in enumerate(dims):
            source = tf.matmul(
                source, tf.get_variable('bw{}'.format(idx), [size, dim]))

            if params.should_add_bias_before_rnn():
                source = source + tf.get_variable('bb{}'.format(idx), [dim])

            if params.should_use_relu_before_rnn() and idx + 1 < len(dims):
                source = tf.nn.relu(source)

            size = dim

        return source

    def build_nn_after_rnn(self, params, source):
        """
        """
        dims = params.get_hidden_layer_dim_after_rnn()
        size = params.get_rnn_unit_num()

        source = tf.concat(1, source)
        source = tf.reshape(source, [-1, params.get_rnn_unit_num()])

        if len(dims) == 0 or dims[-1] != 2:
            dims.append(2)

        for idx, dim in enumerate(dims):
            source = tf.matmul(
                source, tf.get_variable('aw{}'.format(idx), [size, dim]))

            if params.should_add_bias_after_rnn():
                source = source + tf.get_variable('ab{}'.format(idx), [dim])

            if params.should_use_relu_after_rnn() and idx + 1 < len(dims):
                source = tf.nn.relu(source)

            size = dim

        return source

    def build_optimizer(self, params):
        """
        """
        if params.should_use_adam():
            optimizer = tf.train.AdamOptimizer(params.get_learning_rate())
        else:
            raise Exception('need specific optimizer')

        return optimizer

    def save_checkpoint(self):
        """
        """
        saver = tf.train.Saver()

        saver.save(self._session, self._checkpoint_target_path,
                   global_step=self._global_step)

    def train(self, source_wav, target_srt, summary=False):
        """
        """
        sample_wgt = np.ones([self._training_sequence_size])

        sample_wgt[:self._srt_delay_size] = 0.0

        fetches = self._state

        feed = {}

        feed[self._source_data] = \
            [w[:self._training_sequence_size] for w in source_wav]

        state = self._session.run(fetches, feed)

        fetches = [
            self._global_step,
            self._summaries,
            self._loss,
            self._judge,
            self._trainer
        ]

        feed = {
            self._state: state,
            self._batch_sample_weights: sample_wgt,
            self._source_data: source_wav,
            self._target_data: target_srt
        }

        step, summaries, loss, accuracy, _ = self._session.run(fetches, feed)

        if summary:
            self._reporter.add_summary(summaries, step)

        return loss, accuracy

    def test(self, source_wav, target_srt):
        """
        """
        fetches = self._state

        feed = {
            self._source_data: [source_wav[:self._training_sequence_size]]
        }

        state = self._session.run(fetches, feed)

        sample_wgt = np.ones([self._training_sequence_size])

        feed = {
            self._state: state,
            self._batch_sample_weights: sample_wgt,
            self._source_data: None,
            self._target_data: None
        }

        fetches = [
            self._judge,
            self._last_state,
        ]

        num_correctness = 0.0
        num_samples = 0.0
        sequence_size = self._training_sequence_size

        for i in xrange(0, len(source_wav) - sequence_size, sequence_size):
            feed[self._state] = state
            feed[self._source_data] = [source_wav[i:i+sequence_size]]
            feed[self._target_data] = [target_srt[i:i+sequence_size]]

            correctness, state = self._session.run(fetches, feed)

            if i >= self._srt_delay_size:
                num_samples += 1.0
                num_correctness += correctness

        step = self._session.run(self._global_step)

        accuracy = num_correctness / num_samples

        summary_value = [
            tf.Summary.Value(tag="test accuracy", simple_value=accuracy)]

        summary = tf.Summary(value=summary_value)

        self._reporter.add_summary(summary, step)

        print accuracy