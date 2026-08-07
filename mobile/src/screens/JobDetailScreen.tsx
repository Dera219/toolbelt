import { useFocusEffect } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import React, { useCallback, useState } from "react";
import { Image, ScrollView, Text } from "react-native";
import { API_URL } from "../config";
import { ApiError, api, money } from "../api/client";
import type { Job, JobRatings, Offer, Payment } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import type { RootStackParamList } from "../navigation";
import { Badge, Button, Card, ErrorText, Input, Row, Screen, Subtext, Title, colors } from "../ui";

type Props = NativeStackScreenProps<RootStackParamList, "JobDetail">;

export default function JobDetailScreen({ route, navigation }: Props) {
  const { jobId } = route.params;
  const { user, mode } = useAuth();
  const [job, setJob] = useState<Job | null>(null);
  const [offers, setOffers] = useState<Offer[]>([]);
  const [payment, setPayment] = useState<Payment | null>(null);
  const [ratings, setRatings] = useState<JobRatings | null>(null);
  const [offerPrice, setOfferPrice] = useState("");
  const [offerMessage, setOfferMessage] = useState("");
  const [stars, setStars] = useState(5);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isCustomer = user != null && job != null && job.customer_id === user.id;
  const isAssignedWorker = user != null && job != null && job.assigned_worker_id === user.id;
  const myOffer = offers.find((o) => o.worker_id === user?.id) ?? null;

  const load = useCallback(async () => {
    try {
      const j = await api.job(jobId);
      setJob(j);
      const results = await Promise.allSettled([
        api.jobOffers(jobId),
        api.jobPayment(jobId),
        api.jobRatings(jobId),
      ]);
      setOffers(results[0].status === "fulfilled" ? results[0].value : []);
      setPayment(results[1].status === "fulfilled" ? results[1].value : null);
      setRatings(results[2].status === "fulfilled" ? results[2].value : null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load job");
    }
  }, [jobId]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const run = (fn: () => Promise<unknown>) => async () => {
    setError(null);
    setBusy(true);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Action failed");
    } finally {
      setBusy(false);
    }
  };

  if (job == null)
    return (
      <Screen>
        <ErrorText message={error} />
      </Screen>
    );

  const canRate =
    job.status === "completed" && (isCustomer || isAssignedWorker) && ratings?.mine == null;

  return (
    <Screen>
      <Title>{job.title}</Title>
      <Row>
        <Badge label={job.status} />
        <Badge label={job.trade} />
        {job.customer_provides_supplies && <Badge label="supplies provided" />}
      </Row>
      <Subtext>{job.address_text}</Subtext>
      {job.photos.length > 0 && (
        <ScrollView horizontal showsHorizontalScrollIndicator={false}>
          {job.photos.map((photo) => (
            <Image
              key={photo.id}
              source={{ uri: `${API_URL}${photo.url}` }}
              style={{ width: 140, height: 140, borderRadius: 10, marginRight: 8 }}
            />
          ))}
        </ScrollView>
      )}
      {job.description !== "" && <Text style={{ color: colors.text }}>{job.description}</Text>}
      {job.budget_cents != null && (
        <Subtext>Budget: {money(job.budget_cents, job.currency)}</Subtext>
      )}

      {payment != null && (
        <Card>
          <Title>Payment</Title>
          <Row>
            <Badge label={payment.status} />
            <Subtext>{money(payment.amount_cents, payment.currency)}</Subtext>
          </Row>
          {isAssignedWorker && (
            <Subtext>
              You earn {money(payment.worker_net_cents, payment.currency)} after the platform fee.
            </Subtext>
          )}
          {payment.refunded_cents > 0 && (
            <Subtext>Refunded: {money(payment.refunded_cents, payment.currency)}</Subtext>
          )}
        </Card>
      )}

      {/* Customer: review offers while open */}
      {isCustomer && job.status === "open" && (
        <Card>
          <Title>Offers ({offers.filter((o) => o.status === "pending").length})</Title>
          {offers.length === 0 && <Subtext>No offers yet — workers nearby were notified.</Subtext>}
          {offers.map((offer) => (
            <Card key={offer.id}>
              <Row>
                <Title>{money(offer.price_cents, job.currency)}</Title>
                <Badge label={offer.status} />
              </Row>
              {offer.message !== "" && <Subtext>{offer.message}</Subtext>}
              <Row>
                {offer.status === "pending" && (
                  <Button
                    label="Accept & book"
                    onPress={run(() => api.acceptOffer(offer.id))}
                    loading={busy}
                  />
                )}
                <Button
                  label="Chat"
                  variant="secondary"
                  onPress={() =>
                    navigation.navigate("Chat", { jobId: job.id, workerId: offer.worker_id })
                  }
                />
              </Row>
            </Card>
          ))}
        </Card>
      )}

      {/* Worker: make an offer while open */}
      {mode === "worker" && !isCustomer && job.status === "open" && myOffer == null && (
        <Card>
          <Title>Make an offer</Title>
          <Input
            label="Your price (e.g. 110.00)"
            value={offerPrice}
            onChangeText={setOfferPrice}
            keyboardType="decimal-pad"
          />
          <Input
            label="Message (optional)"
            value={offerMessage}
            onChangeText={setOfferMessage}
            autoCapitalize="sentences"
          />
          <Button
            label="Send offer"
            loading={busy}
            onPress={run(async () => {
              const cents = Math.round(parseFloat(offerPrice) * 100);
              if (!Number.isFinite(cents) || cents <= 0)
                throw new ApiError(0, "Enter a valid price");
              await api.makeOffer(job.id, cents, offerMessage.trim());
            })}
          />
        </Card>
      )}
      {myOffer != null && !isCustomer && (
        <Card>
          <Row>
            <Title>Your offer: {money(myOffer.price_cents, job.currency)}</Title>
            <Badge label={myOffer.status} />
          </Row>
          <Button
            label="Chat with customer"
            variant="secondary"
            onPress={() => navigation.navigate("Chat", { jobId: job.id, workerId: myOffer.worker_id })}
          />
        </Card>
      )}

      {/* Lifecycle actions */}
      <Row>
        {isAssignedWorker && job.status === "assigned" && (
          <Button label="Start job" onPress={run(() => api.startJob(job.id))} loading={busy} />
        )}
        {(isCustomer || isAssignedWorker) && job.status === "in_progress" && (
          <Button
            label="Mark completed"
            onPress={run(() => api.completeJob(job.id))}
            loading={busy}
          />
        )}
        {((isCustomer && (job.status === "open" || job.status === "assigned")) ||
          (isAssignedWorker && job.status === "assigned")) && (
          <Button
            label="Cancel job"
            variant="danger"
            onPress={run(() => api.cancelJob(job.id))}
            loading={busy}
          />
        )}
        {(isCustomer || isAssignedWorker) && job.assigned_worker_id != null && (
          <Button
            label="Chat"
            variant="secondary"
            onPress={() =>
              navigation.navigate("Chat", { jobId: job.id, workerId: job.assigned_worker_id! })
            }
          />
        )}
      </Row>

      {/* Ratings */}
      {canRate && (
        <Card>
          <Title>Rate this {isCustomer ? "worker" : "customer"}</Title>
          <Row>
            {[1, 2, 3, 4, 5].map((n) => (
              <Button
                key={n}
                label={n <= stars ? "★" : "☆"}
                variant={n <= stars ? "primary" : "secondary"}
                onPress={() => setStars(n)}
              />
            ))}
          </Row>
          <Input
            label="Comment (optional)"
            value={comment}
            onChangeText={setComment}
            autoCapitalize="sentences"
          />
          <Button
            label="Submit rating"
            loading={busy}
            onPress={run(() => api.rateJob(job.id, stars, comment.trim()))}
          />
        </Card>
      )}
      {ratings?.mine != null && (
        <Card>
          <Subtext>You rated: {"★".repeat(ratings.mine.stars)}</Subtext>
          {ratings.other != null ? (
            <Subtext>
              They rated you: {"★".repeat(ratings.other.stars)}
              {ratings.other.comment ? ` — "${ratings.other.comment}"` : ""}
            </Subtext>
          ) : ratings.other_submitted ? (
            <Subtext>Their rating unlocks when both are in (or after 14 days).</Subtext>
          ) : (
            <Subtext>Waiting for their rating.</Subtext>
          )}
        </Card>
      )}
      <ErrorText message={error} />
    </Screen>
  );
}
