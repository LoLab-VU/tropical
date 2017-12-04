from tropical import clustering
import pickle
from pathos.multiprocessing import ProcessingPool as Pool

with open('earm_signatures.pickle', 'rb') as handle:
    all_signatures = pickle.load(handle)

sil_threshold = 0.8

def get_cluster_percentage_color(signatures_idx):
    signatures = all_signatures[signatures_idx]['consumption']
    clus = clustering.ClusterSequences(data=signatures, unique_sequences=False, truncate_seq=50)
    clus.diss_matrix(n_jobs=4)
    sil_df = clus.silhouette_score_kmeans_range(cluster_range=range(2, 31), n_jobs=4, random_state=1234)
    if sil_df['cluster_silhouette'].max() > sil_threshold:
        # Define n_clus to have the minimum number of clusters when silh scores are too similar
        best_silhs = sil_df.loc[sil_df['cluster_silhouette'] > sil_threshold]
        best_silh, n_clus = best_silhs.loc[best_silhs['num_clusters'].idxmin()]
    else:
        best_silh, n_clus = sil_df.loc[sil_df['cluster_silhouette'].idxmax()]
    n_clus = int(n_clus)
    clus.Kmeans(n_clusters=n_clus, random_state=1234)
    return clus.cluster_percentage_color(best_silh=best_silh)


drivers = all_signatures.keys()
drivers.remove('species_combinations')
p = Pool(4)
res = p.amap(get_cluster_percentage_color, drivers)
results = res.get()

with open('results.pickle', 'wb') as fp:
    pickle.dump(results, fp)
