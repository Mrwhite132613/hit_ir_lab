"""
候选答案句排序.
"""
# todo 去停用词
from util import read_json, seg_line, pos_tag, passages_path, train_path, dump, file_exists, load
from sklearn.feature_extraction.text import TfidfVectorizer
from distance import levenshtein as edit_dist
from scipy.linalg import norm
from numpy import dot

seg_passages_path = './answer_sentence_selection/seg_passages.json'
train_feature_path, test_feature_path = './answer_sentence_selection/train', './answer_sentence_selection/test'
model_path = './answer_sentence_selection/model'
predict_path = './answer_sentence_selection/predictions'


def lc_subsequence(s1, s2):
    m = [[[0, ''] for x in range(len(s2) + 1)] for y in range(len(s1) + 1)]
    for p1 in range(len(s1)):
        for p2 in range(len(s2)):
            if s1[p1] == s2[p2]:  # 字符匹配成功，则该位置的值为左上方的值加1
                m[p1 + 1][p2 + 1] = [m[p1][p2][0] + 1, 'ok']
            elif m[p1 + 1][p2][0] > m[p1][p2 + 1][0]:  # 左值大于上值，则该位置的值为左值，并标记回溯时的方向
                m[p1 + 1][p2 + 1] = [m[p1 + 1][p2][0], 'left']
            else:  # 上值大于左值，则该位置的值为上值，并标记方向up
                m[p1 + 1][p2 + 1] = [m[p1][p2 + 1][0], 'up']
    (p1, p2), s = (len(s1), len(s2)), []
    while m[p1][p2][0]:  # 不为0时
        direction = m[p1][p2][1]
        if direction == 'ok':  # 匹配成功，插入该字符，并向左上角找下一个
            s.append(s1[p1 - 1])
            p1, p2 = p1 - 1, p2 - 1
        elif direction == 'left':  # 根据标记，向左找下一个
            p2 -= 1
        elif direction == 'up':  # 根据标记，向上找下一个
            p1 -= 1
    return len(s) / min(len(s1), len(s2))  # 计算LCS串长度占据的比例


def get_features(q_words: list, ans_words: list, tf_idf_vec):  # q_words为查询词列表；line_words为候选答案句的分词列表
    """
    实词词性，参考 https://ltp.readthedocs.io/zh_CN/latest/appendix.html#id3
    """

    def gram_words(gram):  # todo 使用set加快判断速度
        if gram == 'bigram':
            q_lst = [word0 + word1 for word0, word1 in zip(q_words[:-1], q_words[1:])]
            ans_lst = [word0 + word1 for word0, word1 in zip(ans_words[:-1], ans_words[1:])]
            return len([bi_word for bi_word in q_lst if bi_word in ans_lst]) / (len(ans_lst) + 1)
        else:
            return len([word for word in q_words if word in ans_words]) / len(ans_words)

    res, tags, q, ans = [], {'a', 'n', 'nh', 'ni', 'nl', 'ns', 'nt', 'nz', 'v'}, ''.join(q_words), ''.join(ans_words)
    res.append('1:%d' % len(ans_words))  # 答案句词数
    res.append('2:%d' % len([tag for tag in pos_tag(ans_words) if tag in tags]))  # 答案句实词数
    res.append('3:%d' % abs(len(q_words) - len(ans_words)))  # 问句与答案句词数差异
    res.append('4:%f' % gram_words('unigram'))  # unigram词共现比例
    res.append('5:%f' % gram_words('bigram'))  # bigram词共现比例
    res.append('6:%d' % lc_subsequence(q, ans_words))  # 最长公共子序列长度比例
    res.append('7:%d' % edit_dist(q, ans))  # 编辑距离
    vectors = tf_idf_vec.transform([' '.join(q_words), ' '.join(ans_words)]).toarray()
    norm_val = (norm(vectors[0]) * norm(vectors[1]))
    res.append('8:%f' % ((dot(vectors[0], vectors[1]) / norm_val) if norm_val else 0))  # tf-idf相似度
    return res


def load_train_dev(dev=0.1, update=False):  # 生成训练集和验证集，并将其按照rank-svm数据格式要求写入到文件中
    if file_exists(train_feature_path) and file_exists(test_feature_path) and not update:
        return
    else:
        if file_exists(seg_passages_path):
            seg_passages = load(seg_passages_path)
        else:
            seg_passages = {}
            for item in read_json(passages_path):
                seg_passages[item['pid']] = [seg_line(line.replace(' ', '')) for line in item['document']]
            dump(seg_passages_path, seg_passages)  # 将分词后的文本集导出到文件中

        res_lst, feature_lst = read_json(train_path), []
        for item in res_lst:  # 遍历train.json文件中的每一行query信息
            qid, pid, q_words, ans_words_lst, features = item['qid'], item['pid'], seg_line(item['question']), \
                                                         [seg_line(line) for line in item['answer_sentence']], []

            tf_idf_vec = TfidfVectorizer(token_pattern=r"(?u)\b\w+\b")
            tf_idf_vec.fit_transform(' '.join(word_lst) for word_lst in seg_passages[str(pid)])

            for word_lst in seg_passages[str(pid)]:
                value = 3 if word_lst in ans_words_lst else 0  # 排序用的值 todo
                feature = ' '.join(get_features(q_words, word_lst, tf_idf_vec))
                features.append('%d qid:%d %s' % (value, qid, feature))
            feature_lst.append(features)
        feature_lst.sort(key=lambda lst: int(lst[0].split()[1].split(':')[1]))  # 按照qid排序
        dev_num = int(dev * len(feature_lst))
        train_features, test_features = feature_lst[:-dev_num], feature_lst[-dev_num:]

        # 导出训练集和测试集
        with open(train_feature_path, 'w', encoding='utf-8') as f1, open(test_feature_path, 'w',
                                                                         encoding='utf-8') as f2:
            f1.write('\n'.join([feature for feature_lst in train_features for feature in feature_lst]))
            f2.write('\n'.join([feature for feature_lst in test_features for feature in feature_lst]))
        return train_features, test_features


def exe_rank_svm():  # 调用svm-rank可执行文件，训练并预测模型
    from os import system
    train_cmd = '.\svm_rank_windows\svm_rank_learn.exe -c 10 %s %s' % (train_feature_path, model_path)
    predict_cmd = '.\svm_rank_windows\svm_rank_classify.exe %s %s %s' % (test_feature_path, model_path, predict_path)
    system('%s && %s' % (train_cmd, predict_cmd))


def evaluate(n=1):
    with open(test_feature_path, 'r', encoding='utf-8') as f1, open(predict_path, 'r', encoding='utf-8') as f2:
        y_true, y_predict, right = {}, {}, 0
        for line1, line2 in zip(f1, f2):
            if len(line1) == 1:
                break
            qid = int(line1.split()[1].split(':')[1])
            lst1, lst2 = y_true.get(qid, []), y_predict.get(qid, [])
            lst1.append((int(line1[0]), len(lst1)))
            lst2.append((float(line2.strip()), len(lst2)))
            y_true[qid], y_predict[qid] = lst1, lst2

        for qid in y_true:
            lst1 = sorted(y_true[qid], key=lambda item: item[0], reverse=True)  # 按照val大小排序
            lst2 = sorted(y_predict[qid], key=lambda item: item[0], reverse=True)
            if lst1[0][1] == lst2[0][1]:
                right += 1
        return right, len(y_true)


def main():
    print('*' * 100 + '\n正在构造训练集和开发集特征文件...')
    load_train_dev(update=False)
    print('构造训练集和开发集特征文件完成...')
    print('*' * 100 + '\n开始训练svm-rank模型并对验证集进行预测...')
    exe_rank_svm()
    print('预测完成\n' + '*' * 100)
    right_predict, num = evaluate()
    print('验证集正确答案数目：{}；总数：{}；正确率：{}'.format(right_predict, num, right_predict / num))


if __name__ == '__main__':
    main()
